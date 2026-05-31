# Burn-rate needle + API-key budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a universal live `$X/min` burn-rate field to line 2, make it the single home for the pace arrow/yin-yang needle, and give API-key sessions (no `rate_limits`) a configurable daily-budget needle plus a `day: NN%` position field.

**Architecture:** A new `statusline_lib/burnrate.py` module computes a live 5-minute global spend rate (trailing 300s sum / 5, mirroring llamalab `/spend`) and renders the burn-rate field. The needle on that field is sourced two ways: for subscription sessions (when `rate_limits` carries quota data) it is the existing weekly reset-anchored forecast, relocated off the `wk:` field; for API-key sessions with `STATUSLINE_DAILY_BUDGET` set it is a burst-robust 24h-integral ratio (`trailing_24h_spend / budget`). Spend walks reuse the existing pace discovery/parse helpers and `cost._cost_for_turn`, cached behind a small scalar-sum cache keyed by window start (this supersedes the spec's "multi-key the hourly cache" idea). Detection is purely data-driven: `rate_limits` present -> subscription path, absent -> API-key path.

**Tech Stack:** Python 3.x (stdlib only), the existing `statusline_lib` package (`base`, `cost`, `pace`, `project`, `walker`), standalone `scripts/verify_*.py` test runners (CI globs them), ruff + aislop gates.

---

## File Structure

- **Create** `statusline_lib/burnrate.py` - the burn-rate field: 5-min rate walk, scalar-sum cache, daily-budget parse, 24h-integral needle, `format_burn_rate`, `format_day_budget`. One responsibility: the new burn-rate / daily-budget rendering.
- **Modify** `statusline_lib/pace.py` - extract `_weekly_deltas`, drop the arrow/glyph from the `wk:` render (now number-only), add `weekly_needle` (the relocated subscription needle).
- **Modify** `statusline_lib/__init__.py` - re-export the new public/private names.
- **Modify** `statusline.py` - assemble the two new fields into line 2.
- **Create** `scripts/verify_burn_rate.py` - tests for the new module.
- **Modify** `scripts/verify_quota_render.py` - `wk:` no longer carries the arrow; arrow/glyph now tested via `weekly_needle`.
- **Modify** `README.md` - document the field, `day:`, detection, config, and the cross-session scope note.

Tests inject spend by reassigning module-level walk functions (the established pattern in `verify_quota_render.py`, which reassigns `pace._pace_hourly_cached` and `pace._now_unix`).

---

## Phase 1: Live 5-minute burn-rate field (no needle yet)

### Task 1: Window spend walk + scalar-sum cache

**Files:**
- Create: `statusline_lib/burnrate.py`
- Test: `scripts/verify_burn_rate.py`

- [x] **Step 1: Write the failing test**

Create `scripts/verify_burn_rate.py`:

```python
"""Verify the burn-rate field: live 5-min $/min, daily-budget needle, day: %.

Injects spend by reassigning burnrate._sum_window_spend (the walk seam) and
pinning the clock via pace._now_unix, mirroring verify_quota_render.py.

Run from anywhere; imports from `schoen-claude-status` by path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import statusline_lib.burnrate as burnrate
import statusline_lib.pace as pace
from statusline_lib.base import GREEN, RED, RESET, YELLOW
from statusline_lib.pace import ARROW_DOWN, ARROW_UP, ON_TARGET_GLYPH

_NOW = 1_700_000_000.0


def _with_spend(window_to_total, fn):
    """Run fn() with the spend walk stubbed from a {win_start: total} map and
    the clock pinned to _NOW. burnrate imported _now_unix BY VALUE
    (`from .pace import _now_unix`), so we patch burnrate._now_unix - patching
    pace._now_unix alone would not reach the window-key computation."""
    real_sum = burnrate._sum_window_spend
    real_cached = burnrate._window_spend_cached
    real_now_b = burnrate._now_unix
    real_now_p = pace._now_unix
    burnrate._sum_window_spend = lambda ws: window_to_total.get(int(ws), 0.0)
    burnrate._window_spend_cached = lambda ws: window_to_total.get(int(ws), 0.0)
    burnrate._now_unix = lambda: _NOW
    pace._now_unix = lambda: _NOW
    try:
        return fn()
    finally:
        burnrate._sum_window_spend = real_sum
        burnrate._window_spend_cached = real_cached
        burnrate._now_unix = real_now_b
        pace._now_unix = real_now_p


def _check_cache_dedupes_walk(failures):
    import tempfile

    calls = {"n": 0}
    real_sum = burnrate._sum_window_spend
    real_path = burnrate._SPEND_CACHE_PATH
    fd, tmp = tempfile.mkstemp(prefix="burnrate-cache-")
    os.close(fd)
    os.remove(tmp)  # start with no cache file present
    burnrate._SPEND_CACHE_PATH = tmp
    burnrate._sum_window_spend = lambda ws: (calls.__setitem__("n", calls["n"] + 1), 12.5)[1]
    try:
        a = burnrate._window_spend_cached(_NOW - 300)
        b = burnrate._window_spend_cached(_NOW - 300)
    finally:
        burnrate._sum_window_spend = real_sum
        burnrate._SPEND_CACHE_PATH = real_path
        try:
            os.remove(tmp)
        except OSError:
            pass
    if a != 12.5 or b != 12.5:
        failures.append(f"cache should return the walked total; got {a!r}, {b!r}")
    if calls["n"] != 1:
        failures.append(f"second cached call should not re-walk; walks={calls['n']}")


def check(failures):
    _check_cache_dedupes_walk(failures)


def main():
    failures = []
    check(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("OK: burn-rate field renders rate, needle, and day: budget correctly")


if __name__ == "__main__":
    main()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_burn_rate.py`
Expected: FAIL / ImportError - `statusline_lib.burnrate` does not exist yet.

- [x] **Step 3: Write minimal implementation**

Create `statusline_lib/burnrate.py`:

```python
"""Live burn-rate field: $X/min + budget needle, plus the API-key day: field.

Imports:
  base    -- color constants, color_high_bad
  cost    -- _cost_for_turn (per-turn $)
  pace    -- _now_unix, _discover_pace_groups, _parse_pace_line, arrows/glyph,
             weekly_needle (subscription needle, relocated)
  walker  -- _walker_root_list
"""

import json
import os
from datetime import datetime

from .base import GREEN, RED, RESET, YELLOW, color_high_bad
from .cost import _cost_for_turn
from .pace import (
    ARROW_DOWN,
    ARROW_UP,
    ON_TARGET_GLYPH,
    _discover_pace_groups,
    _now_unix,
    _parse_pace_line,
    weekly_needle,
)
from .walker import _walker_root_list

# Neutral grey for the rate number; the needle glyph carries the verdict color.
RATE_COLOR = "\x1b[38;5;245m"

_SPEND_CACHE_TTL_SECONDS = 15
_SPEND_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", ".statusline-burnrate-cache-v1.json"
)


def _sum_window_spend(win_start):
    """Total funny-money $ across all sessions with a turn ts >= win_start.

    Global / cross-machine: walks every session under _walker_root_list()
    (which includes extra_roots from walker-roots.json). Reuses the pace
    discovery + line parser; dedups message.id within each parent+subagents
    group like the hourly walk does.
    """
    roots = _walker_root_list()
    if not roots:
        return 0.0
    groups = _discover_pace_groups(roots, win_start)
    total = 0.0
    for paths in groups.values():
        seen_ids = set()
        last_model = ""
        for path in paths:
            try:
                with open(path, "rb") as f:
                    for line in f:
                        parsed = _parse_pace_line(line, seen_ids, earliest=win_start)
                        if parsed is None:
                            continue
                        ts, usage, model_id = parsed
                        if model_id:
                            last_model = model_id
                        total += _cost_for_turn(usage, model_id or last_model)
            except OSError:
                continue
    return total


def _window_spend_cached(win_start):
    """15s-TTL scalar cache around _sum_window_spend, multi-keyed by win_start.

    A render asks for up to three windows (5-min, 24h, midnight); one cache file
    holds all of them so they don't evict each other (the spec's hourly-cache
    thrash concern does not apply to this separate scalar cache).
    """
    win_key = str(int(win_start))
    now = _now_unix()
    sums = {}
    try:
        with open(_SPEND_CACHE_PATH, encoding="utf-8") as f:
            cached = json.load(f)
        if now - cached.get("computed_at_unix", 0) < _SPEND_CACHE_TTL_SECONDS:
            sums = cached.get("sums", {}) or {}
            if win_key in sums:
                return sums[win_key]
    except (OSError, ValueError, KeyError):
        sums = {}
    total = _sum_window_spend(win_start)
    sums[win_key] = total
    try:
        with open(_SPEND_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"computed_at_unix": now, "sums": sums}, f)
    except OSError:
        # Best-effort cache write; failure just means we recompute next render.
        pass
    return total
```

- [x] **Step 4: Run test to verify it passes**

Run: `python scripts/verify_burn_rate.py`
Expected: PASS - `OK: burn-rate field renders rate, needle, and day: budget correctly`

- [x] **Step 5: Commit**

```bash
git add statusline_lib/burnrate.py scripts/verify_burn_rate.py
git commit -m "feat(burnrate): window spend walk + scalar-sum cache"
```

### Task 2: 5-minute rate + rate-only render

**Files:**
- Modify: `statusline_lib/burnrate.py`
- Test: `scripts/verify_burn_rate.py`

- [x] **Step 1: Write the failing test**

Add to `scripts/verify_burn_rate.py` (above `def check`):

```python
def _check_five_min_rate_render(failures):
    # $6.00 in the last 5 min -> $1.20/min. No rate_limits, no budget -> no needle.
    out = _with_spend({int(_NOW - 300): 6.0}, lambda: burnrate.format_burn_rate(None))
    if "$1.20/min" not in out:
        failures.append(f"5-min rate should render $1.20/min; got {out!r}")
    if ARROW_UP in out or ARROW_DOWN in out or ON_TARGET_GLYPH in out:
        failures.append(f"no budget / no quota should show no needle; got {out!r}")


def _check_omitted_when_idle_no_budget(failures):
    out = _with_spend({}, lambda: burnrate.format_burn_rate(None))
    if out != "":
        failures.append(f"idle + no budget + no quota should omit the field; got {out!r}")
```

And add to `def check`:

```python
    _check_five_min_rate_render(failures)
    _check_omitted_when_idle_no_budget(failures)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_burn_rate.py`
Expected: FAIL - `format_burn_rate` not defined.

- [x] **Step 3: Write minimal implementation**

Append to `statusline_lib/burnrate.py`:

```python
def _five_min_rate():
    """Live global spend rate in $/min over the trailing 300s (llamalab /spend)."""
    return _window_spend_cached(_now_unix() - 300) / 5.0


def _has_quota(rate_limits):
    """True when the payload carries usable subscription quota data."""
    rl = rate_limits or {}
    for win_key in ("five_hour", "seven_day"):
        if (rl.get(win_key) or {}).get("used_percentage") is not None:
            return True
    return False


def format_burn_rate(rate_limits):
    """Render ` $X.XX/min<needle>` (neutral rate + colored needle), or "".

    Rate is the live 5-min global rate. The needle is the weekly forecast for
    subscription sessions and the daily-budget integral for API-key sessions
    (added in later tasks); here it is empty.
    """
    rate = _five_min_rate()
    subscription = _has_quota(rate_limits)
    budget = None  # daily-budget wiring arrives in Phase 2
    if rate <= 0 and not subscription and budget is None:
        return ""
    needle = ""
    return f"{RATE_COLOR}${rate:.2f}/min{RESET}{needle}"
```

- [x] **Step 4: Run test to verify it passes**

Run: `python scripts/verify_burn_rate.py`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add statusline_lib/burnrate.py scripts/verify_burn_rate.py
git commit -m "feat(burnrate): live 5-min \$/min rate render"
```

### Task 3: Re-export + wire into statusline.py

**Files:**
- Modify: `statusline_lib/__init__.py` (import block + `__all__`)
- Modify: `statusline.py:36` (import) and `statusline.py:160-177` (field assembly)

- [x] **Step 1: Add the re-exports**

In `statusline_lib/__init__.py`, add after the `from .beacon import (...)` block:

```python
from .burnrate import (
    RATE_COLOR,
    _five_min_rate,
    _has_quota,
    _sum_window_spend,
    _window_spend_cached,
    format_burn_rate,
)
```

And add `"format_burn_rate",` to the `__all__` list (in the alphabetical block near the other `format_*` names).

- [x] **Step 2: Wire the field into statusline.py**

In `statusline.py`, add `format_burn_rate` to the `from statusline_lib import (...)` block (after `format_beacon`).

Then in `main()`, after the quota line (`quota_summary = format_quota(d.get("rate_limits"))`, line 161), add:

```python
    burnrate_summary = format_burn_rate(d.get("rate_limits"))
```

And change the `parts` tuple (lines 166-176) to include it between quota and cost:

```python
    parts = [
        s
        for s in (
            model_summary,
            context_summary,
            cache_summary,
            quota_summary,
            burnrate_summary,
            cost_summary,
        )
        if s
    ]
```

- [x] **Step 3: Smoke test the wiring**

Run (subscription-shaped payload; the field should appear even at $0.00 because quota is present):

```bash
echo '{"rate_limits":{"seven_day":{"used_percentage":22,"resets_at":9999999999}},"cost":{"total_cost_usd":1.0}}' | python statusline.py
```

Expected: line 2 contains a grey `$0.00/min` segment between the `wk:` field and the cost.

- [x] **Step 4: Run full verify suite + lint**

Run:

```bash
for t in scripts/verify_*.py; do python "$t" || echo "FAILED: $t"; done
ruff check . && ruff format --check .
```

Expected: all verify scripts print `OK`; ruff reports no findings.

- [x] **Step 5: Commit**

```bash
git add statusline_lib/__init__.py statusline.py
git commit -m "feat(burnrate): wire \$/min field into line 2"
```

---

## Phase 2: API-key daily budget (needle + day: field)

### Task 4: Parse STATUSLINE_DAILY_BUDGET

**Files:**
- Modify: `statusline_lib/burnrate.py`
- Test: `scripts/verify_burn_rate.py`

- [x] **Step 1: Write the failing test**

Add to `scripts/verify_burn_rate.py`:

```python
def _check_budget_parse(failures):
    cases = {"100": 100.0, "49.5": 49.5, "0": None, "-5": None, "abc": None, "": None}
    real = os.environ.get("STATUSLINE_DAILY_BUDGET")
    try:
        for raw, expected in cases.items():
            os.environ["STATUSLINE_DAILY_BUDGET"] = raw
            got = burnrate._daily_budget()
            if got != expected:
                failures.append(f"budget {raw!r} -> {got!r}, expected {expected!r}")
        os.environ.pop("STATUSLINE_DAILY_BUDGET", None)
        if burnrate._daily_budget() is not None:
            failures.append("unset budget should be None")
    finally:
        if real is None:
            os.environ.pop("STATUSLINE_DAILY_BUDGET", None)
        else:
            os.environ["STATUSLINE_DAILY_BUDGET"] = real
```

Add `_check_budget_parse(failures)` to `def check`.

- [x] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_burn_rate.py`
Expected: FAIL - `_daily_budget` not defined.

- [x] **Step 3: Write minimal implementation**

Append to `statusline_lib/burnrate.py`:

```python
def _daily_budget():
    """STATUSLINE_DAILY_BUDGET as a positive float (funny-money $/day), else None.

    Malformed / zero / negative -> None (treated as unset).
    """
    raw = os.environ.get("STATUSLINE_DAILY_BUDGET")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None
```

- [x] **Step 4: Run test to verify it passes**

Run: `python scripts/verify_burn_rate.py`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add statusline_lib/burnrate.py scripts/verify_burn_rate.py
git commit -m "feat(burnrate): parse STATUSLINE_DAILY_BUDGET"
```

### Task 5: Daily-budget needle (24h integral ratio)

**Files:**
- Modify: `statusline_lib/burnrate.py`
- Test: `scripts/verify_burn_rate.py`

- [x] **Step 1: Write the failing test**

Add to `scripts/verify_burn_rate.py`:

```python
def _check_budget_needle_ratio(failures):
    # budget $100/day.
    cases = [
        (40.0, ARROW_DOWN, GREEN),   # well under -> green down
        (100.0, ON_TARGET_GLYPH, GREEN),  # exactly on budget -> yin-yang
        (102.0, ON_TARGET_GLYPH, GREEN),  # within 5% -> still yin-yang
        (120.0, ARROW_UP, YELLOW),   # modestly over -> yellow up
        (200.0, ARROW_UP, RED),      # way over -> red up
    ]
    for spend, glyph, color in cases:
        out = burnrate._budget_needle(spend, 100.0)
        if glyph not in out:
            failures.append(f"24h spend ${spend} vs $100 should show {glyph!r}; got {out!r}")
        if color not in out:
            failures.append(f"24h spend ${spend} vs $100 should be {color!r}; got {out!r}")
    if burnrate._budget_needle(0.0, 100.0) != "":
        failures.append("zero 24h spend should yield no needle")
    if burnrate._budget_needle(50.0, None) != "":
        failures.append("no budget should yield no needle")
```

Add `_check_budget_needle_ratio(failures)` to `def check`.

- [x] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_burn_rate.py`
Expected: FAIL - `_budget_needle` not defined.

- [x] **Step 3: Write minimal implementation**

Append to `statusline_lib/burnrate.py`:

```python
# Needle thresholds (chosen defaults; tune in practice).
_ON_TARGET_RATIO_MARGIN = 0.05  # within +/-5% of budget -> yin-yang
_OVER_BUDGET_RED_RATIO = 1.5  # >= 1.5x budget -> red, else yellow


def _budget_needle(spend_24h, budget):
    """Colored arrow/yin-yang from the 24h spend integral vs the daily budget.

    r = spend_24h / budget. r<1 -> green down (under); |r-1|<=margin -> green
    yin-yang (on budget); r>1 -> up, yellow until 1.5x then red. Empty when there
    is no budget or no 24h spend to judge.
    """
    if not budget or budget <= 0 or spend_24h <= 0:
        return ""
    ratio = spend_24h / budget
    if abs(ratio - 1.0) <= _ON_TARGET_RATIO_MARGIN:
        return f"{GREEN}{ON_TARGET_GLYPH}{RESET}"
    if ratio < 1.0:
        return f"{GREEN}{ARROW_DOWN}{RESET}"
    color = RED if ratio >= _OVER_BUDGET_RED_RATIO else YELLOW
    return f"{color}{ARROW_UP}{RESET}"
```

- [x] **Step 4: Run test to verify it passes**

Run: `python scripts/verify_burn_rate.py`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add statusline_lib/burnrate.py scripts/verify_burn_rate.py
git commit -m "feat(burnrate): 24h-integral daily-budget needle"
```

### Task 6: API-key needle in format_burn_rate + format_day_budget

**Files:**
- Modify: `statusline_lib/burnrate.py`
- Test: `scripts/verify_burn_rate.py`

- [x] **Step 1: Write the failing test**

Add to `scripts/verify_burn_rate.py`:

```python
def _check_api_key_needle_on_rate(failures):
    # No rate_limits, budget $100. 24h spend $40 -> down arrow on the rate.
    # 5-min spend $6 -> $1.20/min.
    windows = {int(_NOW - 300): 6.0, int(_NOW - 86400): 40.0}
    real = os.environ.get("STATUSLINE_DAILY_BUDGET")
    os.environ["STATUSLINE_DAILY_BUDGET"] = "100"
    try:
        out = _with_spend(windows, lambda: burnrate.format_burn_rate(None))
    finally:
        if real is None:
            os.environ.pop("STATUSLINE_DAILY_BUDGET", None)
        else:
            os.environ["STATUSLINE_DAILY_BUDGET"] = real
    if "$1.20/min" not in out:
        failures.append(f"API-key field should show the 5-min rate; got {out!r}")
    if ARROW_DOWN not in out:
        failures.append(f"under-budget 24h should show a down arrow; got {out!r}")


def _check_day_field(failures):
    # budget $100, since-midnight spend $47 -> day: 47%. Compute midnight under
    # the pinned clock (_NOW) so the window key matches _local_midnight_unix().
    from datetime import datetime

    midnight = (
        datetime.fromtimestamp(_NOW)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    real = os.environ.get("STATUSLINE_DAILY_BUDGET")
    os.environ["STATUSLINE_DAILY_BUDGET"] = "100"
    try:
        out = _with_spend({int(midnight): 47.0}, lambda: burnrate.format_day_budget(None))
    finally:
        if real is None:
            os.environ.pop("STATUSLINE_DAILY_BUDGET", None)
        else:
            os.environ["STATUSLINE_DAILY_BUDGET"] = real
    if "day: " not in out or "47%" not in out:
        failures.append(f"day: field should show 47%; got {out!r}")


def _check_day_field_omitted_for_subscription(failures):
    rl = {"seven_day": {"used_percentage": 22, "resets_at": _NOW + 1000}}
    real = os.environ.get("STATUSLINE_DAILY_BUDGET")
    os.environ["STATUSLINE_DAILY_BUDGET"] = "100"
    try:
        out = _with_spend({}, lambda: burnrate.format_day_budget(rl))
    finally:
        if real is None:
            os.environ.pop("STATUSLINE_DAILY_BUDGET", None)
        else:
            os.environ["STATUSLINE_DAILY_BUDGET"] = real
    if out != "":
        failures.append(f"day: must be suppressed when rate_limits present; got {out!r}")
```

Add all three to `def check`.

- [x] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_burn_rate.py`
Expected: FAIL - `format_day_budget` / `_local_midnight_unix` not defined; needle not wired.

- [x] **Step 3: Write minimal implementation**

In `statusline_lib/burnrate.py`, replace the `budget = None` line and the `needle = ""` line inside `format_burn_rate` so the function reads:

```python
def format_burn_rate(rate_limits):
    """Render ` $X.XX/min<needle>` (neutral rate + colored needle), or "".

    Rate is the live 5-min global rate. Needle: weekly forecast for subscription
    sessions, 24h-integral budget ratio for API-key sessions, empty otherwise.
    """
    rate = _five_min_rate()
    subscription = _has_quota(rate_limits)
    budget = None if subscription else _daily_budget()
    if rate <= 0 and not subscription and budget is None:
        return ""
    if subscription:
        needle = weekly_needle(rate_limits)
    elif budget is not None:
        needle = _budget_needle(_window_spend_cached(_now_unix() - 86400), budget)
    else:
        needle = ""
    return f"{RATE_COLOR}${rate:.2f}/min{RESET}{needle}"
```

And append the day-field helpers:

```python
def _local_midnight_unix():
    """Unix ts of the most recent local midnight (start of today, local time)."""
    now_local = datetime.fromtimestamp(_now_unix())
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


def format_day_budget(rate_limits):
    """` day: NN%` (since-midnight global spend / budget), or "".

    Only for API-key sessions (no quota data) with a valid budget. The midnight
    boundary is artificial: it just defines "today"; there is no projection.
    """
    if _has_quota(rate_limits):
        return ""
    budget = _daily_budget()
    if budget is None:
        return ""
    today_spend = _window_spend_cached(_local_midnight_unix())
    pct = 100.0 * today_spend / budget
    return f"day: {color_high_bad(pct, 75, 90)}"
```

- [x] **Step 4: Run test to verify it passes**

Run: `python scripts/verify_burn_rate.py`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add statusline_lib/burnrate.py scripts/verify_burn_rate.py
git commit -m "feat(burnrate): API-key budget needle + day: field"
```

### Task 7: Wire format_day_budget into statusline.py + export

**Files:**
- Modify: `statusline_lib/__init__.py`
- Modify: `statusline.py`

- [x] **Step 1: Export the new names**

In `statusline_lib/__init__.py`, extend the `from .burnrate import (...)` block to also import `_daily_budget`, `_local_midnight_unix`, `format_day_budget`, and add `"format_day_budget",` to `__all__`.

- [x] **Step 2: Wire the field**

In `statusline.py`, add `format_day_budget` to the `from statusline_lib import (...)` block. After the `burnrate_summary = ...` line add:

```python
    day_budget_summary = format_day_budget(d.get("rate_limits"))
```

Insert `day_budget_summary` into the `parts` tuple immediately before `burnrate_summary` (so order is `... quota_summary, day_budget_summary, burnrate_summary, cost_summary`). `quota_summary` and `day_budget_summary` are mutually exclusive (one is always `""`), so both can sit in the list filtered by truthiness.

- [x] **Step 3: Smoke test API-key path**

Run (no `rate_limits`, budget set):

```bash
STATUSLINE_DAILY_BUDGET=100 sh -c 'echo "{\"cost\":{\"total_cost_usd\":1.0}}" | python statusline.py'
```

Expected: line 2 shows `day: 0%` (no real recent spend in the test) and a grey `$0.00/min` with no needle if there is genuinely no 24h spend on the machine (or a colored arrow if there is). No traceback.

- [x] **Step 4: Run verify suite + lint**

Run:

```bash
for t in scripts/verify_*.py; do python "$t" || echo "FAILED: $t"; done
ruff check . && ruff format --check .
```

Expected: all `OK`; ruff clean.

- [x] **Step 5: Commit**

```bash
git add statusline_lib/__init__.py statusline.py
git commit -m "feat(burnrate): wire day: budget field into line 2"
```

---

## Phase 3: Relocate the subscription needle off `wk:`

### Task 8: Extract `_weekly_deltas`, add `weekly_needle`, strip arrow from `wk:`

**Files:**
- Modify: `statusline_lib/pace.py`
- Modify: `statusline_lib/__init__.py`

- [x] **Step 1: Add `_weekly_deltas` and `weekly_needle` to pace.py**

In `statusline_lib/pace.py`, add after `_rate_arrow` (line 246):

```python
def _weekly_deltas(util, resets_at_unix, period_seconds):
    """(cumulative_delta, current_rate_delta, warn_threshold, elapsed) for the
    weekly window, or None when there isn't enough data. Deltas are seconds vs
    reset (positive = surplus). Shared by the wk: number and the relocated needle.
    """
    if util is None or util <= 0 or not resets_at_unix:
        return None
    reset_dt = datetime.fromtimestamp(resets_at_unix, tz=UTC)
    remaining = (reset_dt - datetime.fromtimestamp(_now_unix(), tz=UTC)).total_seconds()
    elapsed = period_seconds - remaining
    if elapsed <= 0 or remaining <= 0:
        return None
    warn_threshold = 0.05 * period_seconds
    win_start = resets_at_unix - period_seconds
    hourly = _pace_hourly_cached(win_start)
    cumulative_delta, current_rate_delta = project_delta(
        hourly, util, elapsed, remaining, period_seconds
    )
    if cumulative_delta is None:
        return None
    return cumulative_delta, current_rate_delta, warn_threshold, elapsed


def weekly_needle(rate_limits):
    """The relocated subscription needle: colored current-rate arrow, or the
    on-target yin-yang, computed from the weekly window. "" when unavailable.

    STATUSLINE_VERBOSE_PACE renders both numeric deltas instead of the glyph.
    """
    rl = rate_limits or {}
    w = rl.get("seven_day") or {}
    deltas = _weekly_deltas(w.get("used_percentage"), w.get("resets_at"), 7 * 86400)
    if deltas is None:
        return ""
    cumulative_delta, current_rate_delta, warn_threshold, elapsed = deltas
    verbose = os.environ.get("STATUSLINE_VERBOSE_PACE") not in (None, "", "0")
    if verbose and current_rate_delta is not None:
        return (
            f" {_fmt_delta(cumulative_delta, warn_threshold)}"
            f"/{_fmt_delta(current_rate_delta, warn_threshold)}"
        )
    if is_on_target(
        cumulative_delta,
        current_rate_delta,
        elapsed,
        margin_seconds=_ON_TARGET_MARGIN_SECONDS,
    ):
        return f"{GREEN}{ON_TARGET_GLYPH}{RESET}"
    return _rate_arrow(cumulative_delta, current_rate_delta, warn_threshold)
```

- [x] **Step 2: Make the `wk:` render number-only**

Replace the `use_trailing` branch body of `_project_pace` (lines 274-293) so the whole function reads:

```python
def _project_pace(util, resets_at_unix, period_seconds, use_trailing=False):
    """Returns ' <+-Hh>' (colored cumulative pace) or '' if not enough data.

    The current-rate arrow / on-target glyph no longer live here -- they moved to
    the burn-rate field via pace.weekly_needle. This function now renders only the
    cumulative-pace number for both the 5h and weekly windows.
    """
    if util is None or util <= 0 or not resets_at_unix:
        return ""
    try:
        if not use_trailing:
            reset_dt = datetime.fromtimestamp(resets_at_unix, tz=UTC)
            remaining = (
                reset_dt - datetime.fromtimestamp(_now_unix(), tz=UTC)
            ).total_seconds()
            elapsed = period_seconds - remaining
            if elapsed <= 0 or remaining <= 0:
                return ""
            warn_threshold = 0.05 * period_seconds
            delta = 100.0 * elapsed / util - period_seconds
            return f" {_fmt_delta(delta, warn_threshold)}"
        deltas = _weekly_deltas(util, resets_at_unix, period_seconds)
        if deltas is None:
            return ""
        cumulative_delta, _current, warn_threshold, _elapsed = deltas
        return f" {_fmt_delta(cumulative_delta, warn_threshold)}"
    except Exception:
        return ""
```

(`format_quota` is unchanged - it still calls `_project_pace`; the weekly field now simply renders the number with no trailing glyph.)

- [x] **Step 3: Export the new pace names**

In `statusline_lib/__init__.py`, add `_weekly_deltas,` and `weekly_needle,` to the `from .pace import (...)` block.

- [x] **Step 4: Verify pace still imports and renders a bare wk number**

Run:

```bash
python -c "import statusline_lib.pace as p; print(repr(p.weekly_needle({'seven_day':{'used_percentage':22,'resets_at':9999999999}})))"
ruff check statusline_lib/pace.py statusline_lib/__init__.py
```

Expected: prints a needle string (an arrow/glyph or `''`); ruff clean.

- [x] **Step 5: Commit**

```bash
git add statusline_lib/pace.py statusline_lib/__init__.py
git commit -m "refactor(pace): relocate weekly needle out of wk: into weekly_needle"
```

### Task 9: Update verify_quota_render + verify subscription needle on the rate

**Files:**
- Modify: `scripts/verify_quota_render.py`
- Modify: `scripts/verify_burn_rate.py`

- [x] **Step 1: Rewrite verify_quota_render.py for the split**

Replace `scripts/verify_quota_render.py` with (asserts `format_quota` is number-only; arrows/glyph now come from `weekly_needle`):

```python
"""Verify the quota render split: format_quota is number-only; the current-rate
arrow and on-target glyph now come from pace.weekly_needle (relocated to the
burn-rate field). Pins the clock + hourly walk, mirroring the burn-rate verifier.

Run from anywhere; imports from `schoen-claude-status` by path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import statusline_lib.pace as pace
from statusline_lib.base import GREEN, RED, RESET, YELLOW
from statusline_lib.pace import ARROW_DOWN, ARROW_UP, ON_TARGET_GLYPH, format_quota, weekly_needle

_HOUR = 3600.0
_WIN_START = 1_700_000_000.0


def _rate_limits(util, period, elapsed_hours):
    resets_at = _WIN_START + period
    return {"seven_day": {"used_percentage": util, "resets_at": resets_at}}, resets_at


def _pin(hourly, elapsed_hours, period_days=7):
    period = period_days * 24 * _HOUR
    now = _WIN_START + elapsed_hours * _HOUR
    real_now, real_hourly = pace._now_unix, pace._pace_hourly_cached
    pace._now_unix = lambda: now
    pace._pace_hourly_cached = lambda _ws: hourly
    rl, _resets = _rate_limits(0, period, elapsed_hours)  # util filled per call
    return period, now, real_now, real_hourly


def _render_quota(util, hourly, elapsed_hours):
    period, now, real_now, real_hourly = _pin(hourly, elapsed_hours)
    rl = {"seven_day": {"used_percentage": util, "resets_at": _WIN_START + period}}
    try:
        return format_quota(rl)
    finally:
        pace._now_unix, pace._pace_hourly_cached = real_now, real_hourly


def _render_needle(util, hourly, elapsed_hours):
    period, now, real_now, real_hourly = _pin(hourly, elapsed_hours)
    rl = {"seven_day": {"used_percentage": util, "resets_at": _WIN_START + period}}
    try:
        return weekly_needle(rl)
    finally:
        pace._now_unix, pace._pace_hourly_cached = real_now, real_hourly


def _check_quota_is_number_only(failures):
    out = _render_quota(30.0, [0.1] * 70 + [50.0] * 14, 84)
    if ARROW_UP in out or ARROW_DOWN in out or ON_TARGET_GLYPH in out:
        failures.append(f"format_quota must be number-only now; got {out!r}")
    if "wk:" not in out:
        failures.append(f"format_quota should still render wk:; got {out!r}")


def _check_quota_number_color_bands(failures):
    green = _render_quota(30.0, [], 84).rsplit(" ", 1)[-1]
    if GREEN not in green or RED in green:
        failures.append(f"large surplus wk number should be GREEN; got {green!r}")
    red = _render_quota(60.0, [], 84).rsplit(" ", 1)[-1]
    if RED not in red:
        failures.append(f"deficit wk number should be RED; got {red!r}")
    if RESET not in green:
        failures.append("colored output must reset")


def _check_needle_hot_up(failures):
    out = _render_needle(30.0, [0.1] * 70 + [50.0] * 14, 84)
    if ARROW_UP not in out:
        failures.append(f"hot recent rate should yield an up arrow; got {out!r}")


def _check_needle_cool_down(failures):
    out = _render_needle(60.0, [50.0] * 14 + [0.1] * 70, 84)
    if ARROW_DOWN not in out:
        failures.append(f"cooling recent rate should yield a down arrow; got {out!r}")


def _check_needle_on_target(failures):
    out = _render_needle(50.0, [1.0] * 84, 84)
    if ON_TARGET_GLYPH not in out:
        failures.append(f"both deltas ~0 + warmup done should show the glyph; got {out!r}")
    if ARROW_UP in out or ARROW_DOWN in out:
        failures.append(f"on-target should replace the arrow; got {out!r}")


def _check_needle_empty_window(failures):
    out = _render_needle(40.0, [], 84)
    if ARROW_UP in out or ARROW_DOWN in out:
        failures.append(f"empty window should omit the arrow; got {out!r}")


def check(failures):
    _check_quota_is_number_only(failures)
    _check_quota_number_color_bands(failures)
    _check_needle_hot_up(failures)
    _check_needle_cool_down(failures)
    _check_needle_on_target(failures)
    _check_needle_empty_window(failures)


def main():
    failures = []
    check(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("OK: format_quota is number-only; weekly_needle carries the arrow/glyph")


if __name__ == "__main__":
    main()
```

- [x] **Step 2: Add a subscription-needle-on-rate test to verify_burn_rate.py**

Add to `scripts/verify_burn_rate.py`:

```python
def _check_subscription_needle_on_rate(failures):
    # Subscription: rate_limits present. weekly_needle is stubbed to a known glyph;
    # format_burn_rate must place it on the $/min and ignore any budget env.
    rl = {"seven_day": {"used_percentage": 22, "resets_at": _NOW + 1000}}
    real_needle = burnrate.weekly_needle
    burnrate.weekly_needle = lambda _rl: f"{GREEN}{ARROW_DOWN}{RESET}"
    real_budget = os.environ.get("STATUSLINE_DAILY_BUDGET")
    os.environ["STATUSLINE_DAILY_BUDGET"] = "100"  # must be IGNORED for subscription
    try:
        out = _with_spend({int(_NOW - 300): 5.0}, lambda: burnrate.format_burn_rate(rl))
    finally:
        burnrate.weekly_needle = real_needle
        if real_budget is None:
            os.environ.pop("STATUSLINE_DAILY_BUDGET", None)
        else:
            os.environ["STATUSLINE_DAILY_BUDGET"] = real_budget
    if "$1.00/min" not in out or ARROW_DOWN not in out:
        failures.append(f"subscription needle should ride the \$/min; got {out!r}")
```

Add `_check_subscription_needle_on_rate(failures)` to `def check`.

- [x] **Step 3: Run both verifiers**

Run:

```bash
python scripts/verify_quota_render.py
python scripts/verify_burn_rate.py
```

Expected: both print `OK`.

- [x] **Step 4: Full suite + lint**

Run:

```bash
for t in scripts/verify_*.py; do python "$t" || echo "FAILED: $t"; done
ruff check . && ruff format --check .
```

Expected: all `OK`; ruff clean.

- [x] **Step 5: Commit**

```bash
git add scripts/verify_quota_render.py scripts/verify_burn_rate.py
git commit -m "test: split quota-render vs weekly_needle; subscription needle on \$/min"
```

---

## Phase 4: Docs + quality gate

### Task 10: README

**Files:**
- Modify: `README.md`

- [x] **Step 1: Update the example block + intro**

In the fenced example near the top (lines 11-17), add the burn-rate segment to the line-2 examples and show the API-key variant. Replace the block with:

```
[hostname] /path/to/cwd (branch)
opus4.8[1m] | 183.7K / 1.00M (18.0%) | 15.41M / 207.4K / 99% hit | 5h: 6% +0.4h wk: 21% +9.7h | $1.02/min↓ | $10.66
# API-key session (no rate_limits), STATUSLINE_DAILY_BUDGET=100:
opus4.8[1m] | 183.7K / 1.00M (18.0%) | 15.41M / 207.4K / 99% hit | day: 47% | $1.02/min↓ | $10.66
```

Update the intro sentence (lines 3-7) to mention the burn rate: append ", a live `$/min` burn rate" to the feature list.

- [x] **Step 2: Add a "Burn rate" subsection under "What you see"**

After the Quota bullet block (after line 77), add:

```markdown
- **Burn rate** - a live `$/min` spend rate over the trailing 5 minutes,
  aggregated across **all** local sessions (cross-machine, via the same walker
  roots the pace walk uses). Funny-money units, like the cost field. It is the
  single home for the pace **needle** (`↑`/`↓`/`☯`): on a subscription the needle
  is the weekly rate-limit forecast (relocated off `wk:`, which keeps its `+Hh`
  cumulative number); on an API-key session with `STATUSLINE_DAILY_BUDGET` set it
  is a burst-robust 24h integral - `↓` when your trailing-24h spend is under the
  daily budget, `↑` when over (red past 1.5x), `☯` when within 5% of it. The rate
  number is neutral grey; the needle carries the verdict color. The 5-min number
  and the 24h needle describe different timescales by design, so they can briefly
  disagree on bursty days.
- **Daily budget (API-key only)** - when there is no `rate_limits` payload and
  `STATUSLINE_DAILY_BUDGET` (funny-money dollars/day) is set, a `day: NN%` field
  shows spend since local midnight as a fraction of the budget. The midnight
  boundary only defines "today" - there is no deadline or projection. This is
  **cross-session today** (every session since midnight), a deliberately different
  scope from the per-session cost figure.
```

- [x] **Step 3: Document detection + config near Requirements**

After the Requirements list, add:

```markdown
### Burn rate & daily budget

The `$/min` burn rate renders in every session. The pace needle's source is
**purely data-driven**: if the payload carries `rate_limits` quota data it's a
subscription (needle = weekly forecast); if not, it's treated as an API-key
session (needle = daily-budget integral, only when a budget is configured).

Set a daily target with `STATUSLINE_DAILY_BUDGET` (funny-money dollars per day,
e.g. `export STATUSLINE_DAILY_BUDGET=100`). It is ignored on subscription
sessions, where the rate limits already define the budget.
```

- [x] **Step 4: Verify no em-dashes were introduced**

Run:

```bash
python -c "import io; s=io.open('README.md',encoding='utf-8').read(); n=s.count(chr(0x2014)); print('em-dashes:', n); import sys; sys.exit(1 if n else 0)"
```

Expected: `em-dashes: 0`, exit 0. (Per the global no-em-dash rule; use ` - ` instead.)

- [x] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README for the burn-rate field, day: budget, and detection"
```

### Task 11: Full gate (ruff + aislop + verify)

- [x] **Step 1: Run the complete gate locally**

Run:

```bash
python -m py_compile statusline.py subagent_statusline.py install.py nudge_200k.py statusline_lib/*.py
ruff check . && ruff format --check .
npm run lint:aislop
for t in scripts/verify_*.py; do echo "=== $t ==="; python "$t" || echo "FAILED: $t"; done
```

Expected: py_compile silent; ruff clean; aislop reports 100/100 (>= threshold); every verify script prints `OK`. Fix any finding before proceeding (treat a failing gate like a failing test).

- [x] **Step 2: Smoke test both layouts end-to-end**

Run:

```bash
echo '{"rate_limits":{"five_hour":{"used_percentage":6,"resets_at":9999999999},"seven_day":{"used_percentage":22,"resets_at":9999999999}},"cost":{"total_cost_usd":10.66}}' | python statusline.py
STATUSLINE_DAILY_BUDGET=100 sh -c 'echo "{\"cost\":{\"total_cost_usd\":10.66}}" | python statusline.py'
```

Expected: first shows `wk: ...` with no arrow plus a `$x.xx/min` (needle if there's local weekly data); second shows `day: NN%` plus `$x.xx/min`. No tracebacks.

- [x] **Step 3: Commit the gate pass**

```bash
git add -A
git commit -m "chore: burn-rate gate pass (ruff/aislop/verify)"
```

- [x] **Step 4: Push to the CI remote**

```bash
git push gitea feat/burn-rate-needle
```

Expected: CI (lint + test jobs) goes green on the branch. Open a PR as the `claude-code` bot when ready.

---

## Self-review notes (for the implementer)

- The spec's "multi-key the hourly cache" wrinkle is intentionally **not** done; the budget/rate walks use the separate `_window_spend_cached` scalar cache instead, so the weekly hourly cache keeps its single key and never thrashes.
- `format_quota` is left calling `_project_pace`; only `_project_pace`'s internals change (number-only). This minimizes blast radius.
- `STATUSLINE_DAILY_BUDGET` is read live each render via `_daily_budget()`; no caching of the env value (cheap, and lets the user change it without restarting).
- Needle constants (`_ON_TARGET_RATIO_MARGIN = 0.05`, `_OVER_BUDGET_RED_RATIO = 1.5`) are the chosen defaults to tune in practice.
