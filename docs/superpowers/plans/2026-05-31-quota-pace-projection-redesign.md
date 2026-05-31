# Quota Pace Projection Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cross-week-contaminated trailing-7-day blend in the weekly quota projection with two window-local signals — cumulative pace and a recency-weighted current-rate forecast — shown as a pace number plus a colored directional arrow, and add a backtest harness that empirically picks the shipped estimator.

**Architecture:** A new pure module `statusline_lib/project.py` holds all projection math (three current-rate estimators behind one interface + a day-1 prior that shrinks deltas toward "on-track"). It is fed live data by `pace.py` and replayed historical data by `scripts/backtest_pace.py` through the *same* `project_delta()` function, so the backtest exercises the shipped path. The weekly walk in `pace.py` is repurposed from two scalar buckets to an hourly `$`-burn series (window-local only); the now-dead trailing-scalar machinery and the native pace bridge are removed. Display gains a colored `↑/↓` arrow driven by the current-rate signal, with both numeric deltas shown under `STATUSLINE_VERBOSE_PACE`.

**Tech Stack:** Python 3.11+ (stdlib only; `orjson` optional via `base._json_loads`), `ProcessPoolExecutor` for the parallel walk, `ruff` + `aislop` quality gates, `scripts/verify_*.py` test harness run by Gitea CI.

---

## Phase 1: Pure projection core (`statusline_lib/project.py`)

Fully unit-testable with synthetic hourly arrays — no I/O. This is the shared core the live path and the backtest both call.

### Task 1: Projection core module

**Files:**
- Create: `statusline_lib/project.py`
- Test: `scripts/verify_pace_projection.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/verify_pace_projection.py`:

```python
"""Verify the pure pace-projection core: estimators, calibration, day-1 prior.

`project_delta` returns two deltas in seconds relative to reset (positive =
surplus / lands after reset; negative = exhausts before reset). The current-rate
delta uses the window's own util/$ ratio to turn recent $/h into %/h, and both
deltas are shrunk toward 0 early in the window by the warmup prior. These checks
feed synthetic hourly arrays so no transcripts or clock are involved.

Run from anywhere; imports from `schoen-claude-status` by path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from statusline_lib.project import (
    DEFAULT_PARAMS,
    current_rate,
    project_delta,
)

_HOUR = 3600.0


def _params(**overrides):
    p = dict(DEFAULT_PARAMS)
    p.update(overrides)
    return p


def _check_estimators_pick_recent(failures):
    """A burn that ramps up reads hotter under every estimator than its flat mean."""
    ramp = [1.0, 1.0, 1.0, 10.0, 10.0, 10.0]
    flat_mean = sum(ramp) / len(ramp)
    for est in ("trailing_hours", "ewma", "recent_slope"):
        rate = current_rate(ramp, _params(estimator=est, window_hours=3, **{"lambda": 0.5}))
        if rate <= flat_mean:
            failures.append(f"{est}: ramp-up rate {rate:.2f} should exceed flat mean {flat_mean:.2f}")


def _check_flat_burn_is_on_pace(failures):
    """Flat burn that has used `util`% over exactly half the window projects to
    ~100% at reset: cumulative delta ~= 0 (after full warmup)."""
    period = 7 * 24 * _HOUR
    elapsed = period / 2
    hourly = [1.0] * int(elapsed // _HOUR)
    cum, _ = project_delta(hourly, 50.0, elapsed, period - elapsed, period, _params(warmup_seconds=1))
    if abs(cum) > 0.02 * period:
        failures.append(f"flat 50%-at-half-window should land ~on-pace, got {cum / _HOUR:.1f}h")


def _check_warmup_shrinks_to_zero(failures):
    """At elapsed << warmup, both deltas are pulled toward 0 regardless of raw rate."""
    period = 7 * 24 * _HOUR
    elapsed = 2 * _HOUR
    hourly = [50.0, 50.0]  # very hot start
    p = _params(warmup_seconds=48 * _HOUR)
    cum, cur = project_delta(hourly, 30.0, elapsed, period - elapsed, period, p)
    cum_raw, cur_raw = project_delta(hourly, 30.0, elapsed, period - elapsed, period, _params(warmup_seconds=1))
    if not (abs(cum) < abs(cum_raw)):
        failures.append("warmup should shrink the cumulative delta toward 0 early in window")
    if cur is not None and cur_raw is not None and not (abs(cur) < abs(cur_raw)):
        failures.append("warmup should shrink the current-rate delta toward 0 early in window")


def _check_degenerate_window(failures):
    """No $ in window => current-rate delta is None (arrow omitted), cumulative still computed."""
    period = 7 * 24 * _HOUR
    elapsed = period / 2
    cum, cur = project_delta([], 50.0, elapsed, period - elapsed, period, _params(warmup_seconds=1))
    if cum is None:
        failures.append("cumulative delta should compute even with empty hourly burn")
    if cur is not None:
        failures.append("current-rate delta should be None when window has no dollars")


def _check_bad_util(failures):
    """util <= 0 or non-positive elapsed/remaining => (None, None)."""
    period = 7 * 24 * _HOUR
    for util, el, rem in ((0.0, period / 2, period / 2), (50.0, 0.0, period), (50.0, period, 0.0)):
        cum, cur = project_delta([1.0], util, el, rem, period, DEFAULT_PARAMS)
        if cum is not None or cur is not None:
            failures.append(f"degenerate inputs util={util} el={el} rem={rem} should give (None, None)")


def check(failures):
    _check_estimators_pick_recent(failures)
    _check_flat_burn_is_on_pace(failures)
    _check_warmup_shrinks_to_zero(failures)
    _check_degenerate_window(failures)
    _check_bad_util(failures)


def main():
    failures = []
    check(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("OK: projection core — estimators favor recent burn, prior shrinks early, calibration + edges hold")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_pace_projection.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'statusline_lib.project'`

- [ ] **Step 3: Write minimal implementation**

Create `statusline_lib/project.py`:

```python
"""Pure quota-pace projection math: two window-local signals.

No I/O and no clock. `pace.py` feeds this live window data and
`scripts/backtest_pace.py` feeds it replayed historical windows through the
SAME `project_delta`, so the backtest exercises the shipped projection path.

Two signals (both sourced from the current rate-limit window only):
  * cumulative pace -- util/elapsed projected to reset (the simple run-rate).
  * current-rate forecast -- a recency-weighted $/h over the recent part of the
    window, calibrated to %/h via the window's own util/$ ratio, projected
    forward. This is the "slow down / go nuts" needle.

Both deltas are shrunk toward 0 ("on track to 100% at reset") early in the
window by a warmup prior, so a fresh window reads ~0h instead of exploding on a
tiny elapsed denominator.

Imports: none from sibling modules (leaf module, like base).
"""

# Shipped defaults. `estimator` is chosen empirically by scripts/backtest_pace.py;
# "ewma" is the placeholder default until that backtest runs.
DEFAULT_PARAMS = {
    "estimator": "ewma",
    "window_hours": 18.0,  # trailing_hours / recent_slope: how much recent tail to use
    "lambda": 0.5,  # ewma: per-hour decay weight (0<lambda<1; lower = faster decay to recent)
    "warmup_seconds": 18 * 3600,  # day-1 prior horizon
}


def _trailing_hours_rate(hourly_burn, params):
    """Mean $/h over the last `window_hours` buckets (clamped to what exists)."""
    if not hourly_burn:
        return 0.0
    n = max(1, int(round(params["window_hours"])))
    recent = hourly_burn[-n:]
    return sum(recent) / len(recent)


def _ewma_rate(hourly_burn, params):
    """Exponentially-weighted mean $/h; the most-recent bucket carries the most weight."""
    if not hourly_burn:
        return 0.0
    decay = params["lambda"]
    weighted = 0.0
    norm = 0.0
    for index, value in enumerate(reversed(hourly_burn)):
        weight = decay**index
        weighted += weight * value
        norm += weight
    return weighted / norm if norm else 0.0


def _recent_slope_rate(hourly_burn, params):
    """Least-squares fit over the recent tail; return the projected next-hour
    level (clamped non-negative). Falls back to the last value for <2 points."""
    n = max(2, int(round(params["window_hours"])))
    recent = hourly_burn[-n:]
    count = len(recent)
    if count < 2:
        return recent[-1] if recent else 0.0
    xs = list(range(count))
    mean_x = sum(xs) / count
    mean_y = sum(recent) / count
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return mean_y
    slope = sum((xs[i] - mean_x) * (recent[i] - mean_y) for i in range(count)) / denom
    return max(0.0, mean_y + slope * (xs[-1] - mean_x + 1))


_ESTIMATORS = {
    "trailing_hours": _trailing_hours_rate,
    "ewma": _ewma_rate,
    "recent_slope": _recent_slope_rate,
}


def current_rate(hourly_burn, params=None):
    """Recent $/h burn via the params-selected estimator (defaults to ewma)."""
    params = params or DEFAULT_PARAMS
    estimator = _ESTIMATORS.get(params["estimator"], _ewma_rate)
    return estimator(hourly_burn, params)


def project_delta(hourly_burn, util, elapsed, remaining, period_seconds, params=None):
    """Return (cumulative_delta, current_rate_delta), both seconds vs reset.

    Positive => surplus (hits 100% after reset); negative => exhausts early.
    `current_rate_delta` is None when calibration is degenerate (no $ in window).
    Both deltas are scaled by the day-1 prior k = min(1, elapsed/warmup_seconds).
    Returns (None, None) on degenerate inputs (util<=0, non-positive elapsed/remaining).
    """
    params = params or DEFAULT_PARAMS
    if util is None or util <= 0 or elapsed <= 0 or remaining <= 0:
        return None, None

    cumulative_delta = 100.0 * elapsed / util - period_seconds

    current_rate_delta = None
    window_dollars = sum(hourly_burn) if hourly_burn else 0.0
    if window_dollars > 0:
        rate_per_hour = current_rate(hourly_burn, params)
        pct_per_hour = rate_per_hour * (util / window_dollars)
        if pct_per_hour > 0:
            seconds_to_full = (100.0 - util) / pct_per_hour * 3600.0
            current_rate_delta = seconds_to_full - remaining

    warmup = params["warmup_seconds"]
    k = min(1.0, elapsed / warmup) if warmup > 0 else 1.0
    cumulative_delta *= k
    if current_rate_delta is not None:
        current_rate_delta *= k
    return cumulative_delta, current_rate_delta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/verify_pace_projection.py`
Expected: PASS — `OK: projection core ...`

- [ ] **Step 5: Lint the new module**

Run: `ruff check statusline_lib/project.py scripts/verify_pace_projection.py && ruff format --check statusline_lib/project.py scripts/verify_pace_projection.py`
Expected: no findings (run `ruff format` then re-check if formatting differs).

- [ ] **Step 6: Commit**

```bash
git add statusline_lib/project.py scripts/verify_pace_projection.py
git commit -m "feat(pace): pure window-local projection core (3 estimators + day-1 prior)"
```

---

## Phase 2: Wire the live statusline to the new core

Repurpose the weekly walk from two scalar buckets to an hourly series, route the weekly projection through `project.py`, render the arrow, and remove the now-dead trailing-scalar machinery.

### Task 2: Hourly in-window bucketizer in `pace.py`

**Files:**
- Modify: `statusline_lib/pace.py`
- Test: `scripts/verify_pace_hourly.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/verify_pace_hourly.py`:

```python
"""Verify the hourly in-window $-burn walk bins transcript turns by hour offset.

Writes a tiny temp transcript with assistant turns at known offsets from a
synthetic window start and asserts the returned hourly series places each turn's
cost in the right bucket (index = floor((ts - win_start)/3600)).

Run from anywhere; imports from `schoen-claude-status` by path.
"""

import json
import os
import sys
import tempfile
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import statusline_lib.pace as pace


def _line(ts_unix, message_id, output_tokens):
    return json.dumps(
        {
            "timestamp": datetime.fromtimestamp(ts_unix, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "message": {
                "role": "assistant",
                "id": message_id,
                "model": "claude-opus-4-8",
                "usage": {"output_tokens": output_tokens},
            },
        }
    )


def _check_hourly_binning(failures):
    win_start = 1_700_000_000.0
    now = win_start + 3 * 3600 + 600  # 3h10m of window elapsed
    # 1M opus output tokens = $25.00 per turn.
    turns = [
        (win_start + 60, "a", 1_000_000),  # hour 0
        (win_start + 3600 + 60, "b", 1_000_000),  # hour 1
        (win_start + 3 * 3600 + 60, "c", 1_000_000),  # hour 3
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "projects")
        slug_dir = os.path.join(root, "slug")
        os.makedirs(slug_dir)
        path = os.path.join(slug_dir, "sess.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for ts, mid, tok in turns:
                f.write(_line(ts, mid, tok) + "\n")

        # Pin roots + clock to the temp tree / synthetic now.
        real_roots = pace._walker_root_list
        real_now = pace._now_unix
        pace._walker_root_list = lambda: [root]
        pace._now_unix = lambda: now
        try:
            hourly = pace._walk_pace_hourly(win_start)
        finally:
            pace._walker_root_list = real_roots
            pace._now_unix = real_now

    if len(hourly) != 4:
        failures.append(f"expected 4 hourly buckets (0..3), got {len(hourly)}")
        return
    expected = [25.0, 25.0, 0.0, 25.0]
    for i, (got, want) in enumerate(zip(hourly, expected, strict=True)):
        if abs(got - want) > 1e-6:
            failures.append(f"bucket {i}: expected ${want:.2f}, got ${got:.2f}")


def check(failures):
    _check_hourly_binning(failures)


def main():
    failures = []
    check(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("OK: hourly walk bins in-window turn cost by hour offset from window start")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_pace_hourly.py`
Expected: FAIL — `AttributeError: module 'statusline_lib.pace' has no attribute '_walk_pace_hourly'` (and `_now_unix`).

- [ ] **Step 3: Add the hourly walk + clock seam to `pace.py`**

In `statusline_lib/pace.py`, add a clock seam near the top (after imports) so tests can pin "now":

```python
def _now_unix():
    """Current unix time. Seam so tests can pin the window clock."""
    return datetime.now(UTC).timestamp()
```

Replace the cache-path constant block. Change:

```python
_PACE_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", ".statusline-pace-cache-v2.json"
)
```

to:

```python
_PACE_HOURLY_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", ".statusline-pace-hourly-cache-v1.json"
)
```

(Leave `_PACE_CACHE_TTL_SECONDS = 15` and its comment in place.)

Add these functions (they reuse the surviving `_parse_pace_line`, `_discover_pace_groups`, and `_cost_for_turn`):

```python
def _pace_hourly_for_file(path, seen_ids, win_start_unix, n_buckets):
    """Per-file hourly $-burn list, length n_buckets, indexed from window start."""
    buckets = [0.0] * n_buckets
    last_model = ""
    try:
        with open(path, "rb") as f:
            for line in f:
                parsed = _parse_pace_line(line, seen_ids, win_start_unix)
                if parsed is None:
                    continue
                ts, usage, model_id = parsed
                if model_id:
                    last_model = model_id
                index = int((ts - win_start_unix) // 3600)
                if 0 <= index < n_buckets:
                    buckets[index] += _cost_for_turn(usage, model_id or last_model)
    except OSError:
        return [0.0] * n_buckets
    return buckets


def _walk_session_hourly(paths, win_start_unix, n_buckets):
    """Hourly $-burn for one parent+subagents group. Module-level so a
    ProcessPoolExecutor can serialize it. Shared `seen_ids` across the group's
    files dedups the parent <-> auto-compact-subagent message.id overlap."""
    seen_ids = set()
    totals = [0.0] * n_buckets
    for path in paths:
        per_file = _pace_hourly_for_file(path, seen_ids, win_start_unix, n_buckets)
        for i in range(n_buckets):
            totals[i] += per_file[i]
    return totals


def _sum_hourly(into, addend):
    for i in range(len(into)):
        into[i] += addend[i]


def _walk_hourly_inline(groups, win_start_unix, n_buckets):
    totals = [0.0] * n_buckets
    for paths in groups.values():
        _sum_hourly(totals, _walk_session_hourly(paths, win_start_unix, n_buckets))
    return totals


def _walk_hourly_parallel(groups, win_start_unix, n_buckets):
    workers = min(8, os.cpu_count() or 4)
    totals = [0.0] * n_buckets
    try:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_walk_session_hourly, paths, win_start_unix, n_buckets)
                for paths in groups.values()
            ]
            for fut in as_completed(futures):
                try:
                    _sum_hourly(totals, fut.result())
                except Exception:
                    continue
    except (OSError, RuntimeError):
        return _walk_hourly_inline(groups, win_start_unix, n_buckets)
    return totals


def _walk_pace_hourly(win_start_unix):
    """Hourly in-window $-burn series from window start to now.

    Index 0 is the first hour of the window. Window-local only -- no trailing
    cross-week bucket (the redesign dropped it). The native claude-walker bridge
    is not used here because it returns scalars, not an hourly series.
    """
    roots = _walker_root_list()
    if not roots:
        return []
    now = _now_unix()
    n_buckets = max(1, int((now - win_start_unix) // 3600) + 1)
    groups = _discover_pace_groups(roots, win_start_unix)
    if not groups:
        return [0.0] * n_buckets
    if len(groups) <= 2:
        return _walk_hourly_inline(groups, win_start_unix, n_buckets)
    return _walk_hourly_parallel(groups, win_start_unix, n_buckets)


def _pace_hourly_cached(win_start_unix):
    """15s-TTL cache around _walk_pace_hourly (statusline fires many times/render)."""
    try:
        with open(_PACE_HOURLY_CACHE_PATH, encoding="utf-8") as f:
            cached = json.load(f)
        age = _now_unix() - cached.get("computed_at_unix", 0)
        if age < _PACE_CACHE_TTL_SECONDS and cached.get("win_start_unix") == win_start_unix:
            return cached["hourly"]
    except (OSError, ValueError, KeyError):
        pass
    hourly = _walk_pace_hourly(win_start_unix)
    try:
        with open(_PACE_HOURLY_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "computed_at_unix": _now_unix(),
                    "win_start_unix": win_start_unix,
                    "hourly": hourly,
                },
                f,
            )
    except OSError:
        pass
    return hourly
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/verify_pace_hourly.py`
Expected: PASS — `OK: hourly walk bins in-window turn cost by hour offset from window start`

- [ ] **Step 5: Commit**

```bash
git add statusline_lib/pace.py scripts/verify_pace_hourly.py
git commit -m "feat(pace): hourly in-window burn walk (replaces scalar trailing buckets)"
```

### Task 3: Route the weekly projection through `project.py` and render the arrow

**Files:**
- Modify: `statusline_lib/pace.py`
- Modify: `statusline_lib/__init__.py` (export new symbols if the verify script imports from the package)
- Test: `scripts/verify_quota_render.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/verify_quota_render.py`:

```python
"""Verify the weekly quota render: pace number + colored current-rate arrow.

Drives `format_quota` with a synthetic rate_limits payload and a pinned clock +
pinned hourly walk, asserting: the cumulative-pace number is colored by its own
threshold; a hotter current rate yields a (worse) up arrow, a cooler one a down
arrow; the arrow is omitted when the window has no dollars; and STATUSLINE_VERBOSE_PACE
swaps the arrow for an explicit second number.

Run from anywhere; imports from `schoen-claude-status` by path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import statusline_lib.pace as pace
from statusline_lib.base import GREEN, RED, RESET
from statusline_lib.pace import ARROW_DOWN, ARROW_UP, format_quota

_HOUR = 3600.0


def _rate_limits(util, win_start, period):
    return {
        "seven_day": {"used_percentage": util, "resets_at": win_start + period},
        # 5h omitted -> only the weekly part is rendered.
    }


def _render(util, hourly, elapsed_hours, period_days=7):
    period = period_days * 24 * _HOUR
    win_start = 1_700_000_000.0
    now = win_start + elapsed_hours * _HOUR
    real_now = pace._now_unix
    real_hourly = pace._pace_hourly_cached
    pace._now_unix = lambda: now
    pace._pace_hourly_cached = lambda _ws: hourly
    try:
        return format_quota(_rate_limits(util, win_start, period))
    finally:
        pace._now_unix = real_now
        pace._pace_hourly_cached = real_hourly


def _check_hot_rate_up_arrow(failures):
    # Half the week elapsed, only 30% used (cumulative surplus -> green), but the
    # recent hours are scorching -> current rate hotter than cumulative -> up arrow.
    elapsed_h = 84  # half of 168h
    hourly = [0.1] * 70 + [50.0] * 14  # cool early, blazing recent
    out = _render(30.0, hourly, elapsed_h)
    if ARROW_UP not in out:
        failures.append(f"hot recent rate should render an up arrow; got {out!r}")


def _check_cool_rate_down_arrow(failures):
    # Front-loaded: binged early, idle recently -> current rate cooler -> down arrow.
    elapsed_h = 84
    hourly = [50.0] * 14 + [0.1] * 70
    out = _render(60.0, hourly, elapsed_h)
    if ARROW_DOWN not in out:
        failures.append(f"cooling recent rate should render a down arrow; got {out!r}")


def _check_no_dollars_no_arrow(failures):
    out = _render(40.0, [], 84)
    if ARROW_UP in out or ARROW_DOWN in out:
        failures.append(f"empty window should omit the arrow; got {out!r}")


def _check_verbose_two_numbers(failures):
    os.environ["STATUSLINE_VERBOSE_PACE"] = "1"
    try:
        out = _render(30.0, [0.1] * 70 + [50.0] * 14, 84)
    finally:
        del os.environ["STATUSLINE_VERBOSE_PACE"]
    if "/" not in out:
        failures.append(f"verbose mode should show two slash-separated deltas; got {out!r}")
    if ARROW_UP in out or ARROW_DOWN in out:
        failures.append(f"verbose mode should drop the arrow; got {out!r}")


def _check_number_colored(failures):
    out = _render(30.0, [1.0] * 84, 84)
    if GREEN not in out and RED not in out:
        failures.append(f"pace number should be threshold-colored; got {out!r}")
    if RESET not in out:
        failures.append("colored output must reset")


def check(failures):
    _check_hot_rate_up_arrow(failures)
    _check_cool_rate_down_arrow(failures)
    _check_no_dollars_no_arrow(failures)
    _check_verbose_two_numbers(failures)
    _check_number_colored(failures)


def main():
    failures = []
    check(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("OK: weekly render shows pace number + current-rate arrow (verbose swaps in 2nd number)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_quota_render.py`
Expected: FAIL — `ImportError: cannot import name 'ARROW_UP' from 'statusline_lib.pace'`.

- [ ] **Step 3: Rewrite the projection + render in `pace.py`**

In `statusline_lib/pace.py`, the import block must end up exactly as below — it
adds `project_delta` and drops the removed native bridge; do NOT add any new
`walker` import beyond `_walker_root_list`:

```python
from .base import GREEN, RED, RESET, YELLOW, _json_loads, color_high_bad
from .cost import _cost_for_turn
from .project import project_delta
from .walker import _walker_root_list
```

Add arrow glyph constants near the color usage (top of module, after imports):

```python
# Current-rate arrow glyphs. Up = current rate is HOTTER than cumulative pace
# (eating your buffer -> slow down); down = cooler (building buffer -> go nuts).
ARROW_UP = "↑"
ARROW_DOWN = "↓"
```

Replace `_fmt_delta_hours` (keep it) and add helpers + rewrite `_project_pace`:

```python
def _delta_color(delta, warn_threshold):
    if delta < 0:
        return RED
    if delta <= warn_threshold:
        return YELLOW
    return GREEN


def _fmt_delta(delta, warn_threshold):
    return f"{_delta_color(delta, warn_threshold)}{_fmt_delta_hours(delta)}{RESET}"


def _rate_arrow(cumulative_delta, current_rate_delta, warn_threshold):
    """Colored arrow from the current-rate signal, or '' if unavailable.

    Direction: up when the current rate is hotter than the cumulative pace
    (lands earlier -> eating buffer), down when cooler. Color: the current-rate
    delta's own threshold verdict.
    """
    if current_rate_delta is None:
        return ""
    direction = ARROW_UP if current_rate_delta < cumulative_delta else ARROW_DOWN
    return f"{_delta_color(current_rate_delta, warn_threshold)}{direction}{RESET}"


def _project_pace(util, resets_at_unix, period_seconds, use_trailing=False):
    """Returns ' <±Hh>[arrow]' (colored) or '' if not enough data.

    5h window (use_trailing=False): pure in-window run-rate, unchanged.
    Weekly (use_trailing=True): cumulative-pace number + a colored current-rate
    arrow, both sourced from the current window via project.project_delta over an
    hourly burn series. STATUSLINE_VERBOSE_PACE shows both numeric deltas instead
    of the arrow.
    """
    if util is None or util <= 0 or not resets_at_unix:
        return ""
    try:
        reset_dt = datetime.fromtimestamp(resets_at_unix, tz=UTC)
        remaining = (reset_dt - datetime.fromtimestamp(_now_unix(), tz=UTC)).total_seconds()
        elapsed = period_seconds - remaining
        if elapsed <= 0 or remaining <= 0:
            return ""
        warn_threshold = 0.05 * period_seconds

        if not use_trailing:
            delta = 100.0 * elapsed / util - period_seconds
            return f" {_fmt_delta(delta, warn_threshold)}"

        win_start = resets_at_unix - period_seconds
        hourly = _pace_hourly_cached(win_start)
        cumulative_delta, current_rate_delta = project_delta(
            hourly, util, elapsed, remaining, period_seconds
        )
        if cumulative_delta is None:
            return ""
        number = _fmt_delta(cumulative_delta, warn_threshold)
        if os.environ.get("STATUSLINE_VERBOSE_PACE") and current_rate_delta is not None:
            return f" {number}/{_fmt_delta(current_rate_delta, warn_threshold)}"
        return f" {number}{_rate_arrow(cumulative_delta, current_rate_delta, warn_threshold)}"
    except Exception:
        return ""
```

- [ ] **Step 4: Export new symbols if needed**

If `statusline_lib/__init__.py` re-exports pace symbols (check with `grep -n "ARROW\|format_quota\|_project_pace" statusline_lib/__init__.py`), add `ARROW_UP`, `ARROW_DOWN` to the export list to match the import style of sibling verify scripts. (The verify script imports `from statusline_lib.pace import ...` directly, so this is only needed if you prefer package-level exports — otherwise skip.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python scripts/verify_quota_render.py`
Expected: PASS — `OK: weekly render shows pace number + current-rate arrow ...`

- [ ] **Step 6: Run the full verify suite + lint**

Run: `for t in scripts/verify_*.py; do echo "=== $t ==="; python "$t" || break; done`
Expected: every script prints `OK: ...`.
Run: `ruff check . && ruff format --check .`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add statusline_lib/pace.py statusline_lib/__init__.py scripts/verify_quota_render.py
git commit -m "feat(pace): weekly projection = cumulative number + current-rate arrow"
```

### Task 4: Remove the dead trailing-scalar machinery and native pace bridge

**Files:**
- Modify: `statusline_lib/pace.py` (delete unused functions)
- Modify: `statusline_lib/walker.py` (delete `_walk_pace_buckets_native`)
- Modify: `statusline_lib/__init__.py` (drop any re-exports of deleted symbols)

- [ ] **Step 1: Confirm the symbols are unused**

Run: `grep -rn "_pace_buckets_cached\|_walk_pace_buckets\|_walk_session_group\|_pace_costs_for_file\|_walk_groups_inline\|_walk_groups_parallel\|_walk_pace_buckets_native" statusline_lib scripts statusline.py subagent_statusline.py`
Expected: the only remaining references are the definitions themselves (no callers outside Task-2's new code). If any live caller exists, stop and reconcile before deleting.

- [ ] **Step 2: Delete the dead functions**

From `statusline_lib/pace.py` delete: `_pace_buckets_cached`, `_walk_pace_buckets`, `_walk_session_group`, `_pace_costs_for_file`, `_walk_groups_inline`, `_walk_groups_parallel`. Keep `_parse_pace_line`, `_discover_pace_groups`, and all Task-2 hourly functions.

From `statusline_lib/walker.py` delete `_walk_pace_buckets_native` and remove it from any re-export. (Leave `_find_walker_binary`, `_walker_root_list`, `_walker_subcommand` — still used elsewhere.)

If `statusline_lib/__init__.py` re-exports any deleted name, remove those entries.

- [ ] **Step 3: Run the full verify suite + py_compile + gates**

Run: `python -m py_compile statusline.py subagent_statusline.py install.py statusline_lib/*.py`
Expected: no output (clean compile).
Run: `for t in scripts/verify_*.py; do echo "=== $t ==="; python "$t" || break; done`
Expected: all `OK`.
Run: `ruff check . && ruff format --check . && aislop scan .`
Expected: ruff 0 findings; aislop reports no new findings (no dead code, no unused imports).

- [ ] **Step 4: Smoke-test the real statusline render**

Run (PowerShell, real terminal, to confirm the `↑/↓` glyphs encode correctly on Windows — see the CLAUDE.md Windows stdout-encoding gotcha):
```
echo '{"rate_limits":{"seven_day":{"used_percentage":40,"resets_at":9999999999}}}' | python statusline.py
```
Expected: a `wk: 40% ...` segment renders with an arrow and no `UnicodeEncodeError`. If it errors, ensure `statusline.py` sets UTF-8 stdout (`sys.stdout.reconfigure(encoding="utf-8")`) — add it if absent and note it in the commit.

- [ ] **Step 5: Commit**

```bash
git add statusline_lib/pace.py statusline_lib/walker.py statusline_lib/__init__.py
git commit -m "refactor(pace): drop dead trailing-scalar walk + native pace bridge"
```

---

## Phase 3: Backtest harness (empirically pick the estimator)

A standalone dev tool that replays the candidate estimators against reconstructed, normalized historical windows through the *same* `project_delta`, then prints a comparison table so the winning `params` becomes the shipped default.

### Task 5: Reconstruct + normalize historical windows

**Files:**
- Create: `scripts/backtest_pace.py`
- Test: `scripts/verify_backtest_pace.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/verify_backtest_pace.py`:

```python
"""Verify the backtest harness reconstruction + scoring on a synthetic corpus.

Writes a temp transcript spanning a single known window, then checks that:
  * reconstruct_window bins the spend into an hourly series whose length matches
    the window and whose sum equals the total turn cost;
  * normalize_to_full turns the cumulative curve into a 0..100 synthetic-util
    series ending at exactly 100;
  * score_candidate runs project_delta across the window and returns finite
    convergence / jumpiness / false-call metrics.

Run from anywhere; imports from `schoen-claude-status` by path.
"""

import json
import math
import os
import sys
import tempfile
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.backtest_pace as bt
from statusline_lib.project import DEFAULT_PARAMS

_HOUR = 3600.0


def _line(ts_unix, message_id, output_tokens):
    return json.dumps(
        {
            "timestamp": datetime.fromtimestamp(ts_unix, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "message": {
                "role": "assistant",
                "id": message_id,
                "model": "claude-opus-4-8",
                "usage": {"output_tokens": output_tokens},
            },
        }
    )


def _check_reconstruct_and_score(failures):
    period = 7 * 24 * _HOUR
    win_start = 1_700_000_000.0
    win_end = win_start + period
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "projects", "slug")
        os.makedirs(root)
        path = os.path.join(root, "sess.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            # one $25 turn every 24h -> 7 turns, total $175.
            for day in range(7):
                f.write(_line(win_start + day * 24 * _HOUR + 60, f"d{day}", 1_000_000) + "\n")

        hourly = bt.reconstruct_window([root], win_start, win_end)
        if len(hourly) != int(period // 3600):
            failures.append(f"hourly length {len(hourly)} != {int(period // 3600)} window hours")
        if abs(sum(hourly) - 175.0) > 1e-6:
            failures.append(f"window total should be $175.00, got ${sum(hourly):.2f}")

        util_series = bt.normalize_to_full(hourly)
        if abs(util_series[-1] - 100.0) > 1e-6:
            failures.append(f"normalized util should end at 100, got {util_series[-1]:.3f}")
        if any(b - a < -1e-9 for a, b in zip(util_series, util_series[1:], strict=False)):
            failures.append("normalized util must be monotonically non-decreasing")

        metrics = bt.score_candidate(hourly, util_series, period, DEFAULT_PARAMS)
        for key in ("convergence_mae_h", "jumpiness_h", "false_calls"):
            if key not in metrics or not math.isfinite(metrics[key]):
                failures.append(f"score_candidate missing/non-finite metric: {key}")


def check(failures):
    _check_reconstruct_and_score(failures)


def main():
    failures = []
    check(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("OK: backtest reconstruct + normalize + score produce sane, finite metrics")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_backtest_pace.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backtest_pace'` (and missing `scripts/__init__.py`).

If `scripts/` lacks an `__init__.py`, create an empty one so `import scripts.backtest_pace` resolves:
```bash
test -f scripts/__init__.py || : > scripts/__init__.py
```
(Confirm this doesn't break the CI `for t in scripts/verify_*.py` loop — it does not; the glob only matches `verify_*.py`.)

- [ ] **Step 3: Implement reconstruction + scoring core**

Create `scripts/backtest_pace.py`:

```python
"""Backtest harness: replay candidate current-rate estimators against
reconstructed, normalized historical quota windows.

Why this exists: the live util% is never logged (it arrives in the statusline
payload and is discarded), so we cannot backtest against real historical %.
Instead we reconstruct each past window's cumulative DOLLAR curve from the
transcripts and normalize it to 100% at the window's end -- valid *because* the
user maxes the weekly quota every cycle, so weekly-total $ ~= quota. This makes
the comparison between estimators fair (they all see identical synthetic data)
even though the absolute % is not calibrated. Reset boundaries are supplied by
the user (--resets) because the Opus-4.8 mid-week reset broke the clean 7-day
cadence; the anomalous window is excluded, not modeled.

The replay calls statusline_lib.project.project_delta -- the SAME function the
live statusline uses -- so the winning params transfer directly.
"""

import argparse
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from statusline_lib.cost import _cost_for_turn
from statusline_lib.pace import _discover_pace_groups, _parse_pace_line
from statusline_lib.project import DEFAULT_PARAMS, project_delta
from statusline_lib.walker import _walker_root_list

_HOUR = 3600.0
_CANDIDATES = [
    {"label": "trailing-12h", "estimator": "trailing_hours", "window_hours": 12.0, "lambda": 0.5, "warmup_seconds": 18 * 3600},
    {"label": "trailing-24h", "estimator": "trailing_hours", "window_hours": 24.0, "lambda": 0.5, "warmup_seconds": 18 * 3600},
    {"label": "ewma-0.5", "estimator": "ewma", "window_hours": 18.0, "lambda": 0.5, "warmup_seconds": 18 * 3600},
    {"label": "ewma-0.8", "estimator": "ewma", "window_hours": 18.0, "lambda": 0.8, "warmup_seconds": 18 * 3600},
    {"label": "slope-18h", "estimator": "recent_slope", "window_hours": 18.0, "lambda": 0.5, "warmup_seconds": 18 * 3600},
]


def reconstruct_window(roots, win_start_unix, win_end_unix):
    """Hourly $-burn series for [win_start, win_end). Reuses the live walk's
    discovery/parse/cost helpers so reconstruction matches the live walk."""
    n_buckets = max(1, int((win_end_unix - win_start_unix) // 3600))
    buckets = [0.0] * n_buckets
    groups = _discover_pace_groups(roots, win_start_unix)
    for paths in groups.values():
        seen_ids = set()
        last_model = ""
        for path in paths:
            try:
                with open(path, "rb") as f:
                    for line in f:
                        parsed = _parse_pace_line(line, seen_ids, win_start_unix)
                        if parsed is None:
                            continue
                        ts, usage, model_id = parsed
                        if model_id:
                            last_model = model_id
                        if ts >= win_end_unix:
                            continue
                        index = int((ts - win_start_unix) // 3600)
                        if 0 <= index < n_buckets:
                            buckets[index] += _cost_for_turn(usage, model_id or last_model)
            except OSError:
                continue
    return buckets


def normalize_to_full(hourly):
    """Cumulative $-curve -> 0..100 synthetic util series ending at 100.

    The user maxes the quota every week, so the window total maps to 100%.
    Returns [] if the window had no spend (cannot normalize)."""
    total = sum(hourly)
    if total <= 0:
        return []
    util = []
    running = 0.0
    for value in hourly:
        running += value
        util.append(100.0 * running / total)
    return util


def score_candidate(hourly, util_series, period_seconds, params):
    """Replay project_delta hour-by-hour; return convergence/jumpiness/false-calls.

    * convergence_mae_h: mean |cumulative_delta| over the final 25% of the
      window, in hours (lower = locks onto the true ~0 landing sooner/steadier).
    * jumpiness_h: mean |Δ current_rate_delta| hour-to-hour, in hours.
    * false_calls: sign flips of current_rate_delta that revert within 6 hours
      (a "slow down"/"go nuts" call the next few hours contradicted).
    """
    deltas_cum = []
    deltas_cur = []
    n = len(util_series)
    for t in range(1, n + 1):
        elapsed = t * _HOUR
        remaining = period_seconds - elapsed
        if remaining <= 0:
            break
        util = util_series[t - 1]
        cum, cur = project_delta(hourly[:t], util, elapsed, remaining, period_seconds, params)
        deltas_cum.append(0.0 if cum is None else cum)
        deltas_cur.append(cur)

    tail_start = int(0.75 * len(deltas_cum))
    tail = deltas_cum[tail_start:] or deltas_cum
    convergence = sum(abs(d) for d in tail) / len(tail) / _HOUR if tail else float("inf")

    cur_vals = [d for d in deltas_cur if d is not None]
    jumps = [abs(b - a) for a, b in zip(cur_vals, cur_vals[1:], strict=False)]
    jumpiness = (sum(jumps) / len(jumps) / _HOUR) if jumps else 0.0

    false_calls = _count_reverting_flips(cur_vals, horizon=6)

    return {
        "convergence_mae_h": convergence,
        "jumpiness_h": jumpiness,
        "false_calls": false_calls,
    }


def _count_reverting_flips(values, horizon):
    """Count sign changes that revert to the original sign within `horizon` steps."""
    flips = 0
    for i in range(1, len(values)):
        a, b = values[i - 1], values[i]
        if (a < 0) == (b < 0):
            continue
        look = values[i + 1 : i + 1 + horizon]
        if any((c < 0) == (a < 0) for c in look):
            flips += 1
    return flips
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/verify_backtest_pace.py`
Expected: PASS — `OK: backtest reconstruct + normalize + score produce sane, finite metrics`

- [ ] **Step 5: Commit**

```bash
git add scripts/backtest_pace.py scripts/verify_backtest_pace.py scripts/__init__.py
git commit -m "feat(backtest): reconstruct + normalize + score historical quota windows"
```

### Task 6: CLI driver — segment windows from reset anchors and print the comparison table

**Files:**
- Modify: `scripts/backtest_pace.py` (add `parse_resets`, `iter_windows`, `main`)
- Test: extend `scripts/verify_backtest_pace.py`

- [ ] **Step 1: Add the failing test for window segmentation**

Append to `scripts/verify_backtest_pace.py` `check()` a new function:

```python
def _check_window_segmentation(failures):
    # Three weekly resets => two full windows; the middle one excluded by range.
    period = 7 * 24 * _HOUR
    r0 = 1_700_000_000.0
    resets = [r0, r0 + period, r0 + 2 * period, r0 + 3 * period]
    windows = list(bt.iter_windows(resets, period_seconds=period, exclude=(r0 + period, r0 + 2 * period)))
    # 3 candidate windows (between 4 anchors), minus the one whose start falls in exclude.
    starts = [w[0] for w in windows]
    if (r0 + period) in starts:
        failures.append("excluded window should not be yielded")
    for start, end in windows:
        if abs((end - start) - period) > 1e-6:
            failures.append(f"window {start}->{end} should span exactly one period")
```

And register it in `check()`:
```python
    _check_window_segmentation(failures)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/verify_backtest_pace.py`
Expected: FAIL — `AttributeError: module 'scripts.backtest_pace' has no attribute 'iter_windows'`.

- [ ] **Step 3: Implement the CLI + segmentation**

Append to `scripts/backtest_pace.py`:

```python
def parse_resets(raw):
    """Comma-separated ISO timestamps -> sorted list of unix floats."""
    out = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        dt = datetime.fromisoformat(token.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        out.append(dt.timestamp())
    return sorted(out)


def iter_windows(resets, period_seconds, exclude=None):
    """Yield (win_start, win_end) for each adjacent reset pair spanning ~one
    period. Skips a window whose start is inside the exclude (start, end) range."""
    for start, end in zip(resets, resets[1:], strict=False):
        if abs((end - start) - period_seconds) > 0.5 * period_seconds:
            # gap not ~one period (e.g. the Opus anomaly straddle) -> skip
            continue
        if exclude and exclude[0] <= start < exclude[1]:
            continue
        yield start, end


def _format_table(rows):
    header = f"{'candidate':<14}{'conv(h)':>10}{'jump(h)':>10}{'false':>8}{'windows':>9}"
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['label']:<14}{row['convergence_mae_h']:>10.2f}"
            f"{row['jumpiness_h']:>10.2f}{row['false_calls']:>8}{row['windows']:>9}"
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resets", required=True, help="comma-separated ISO reset timestamps")
    parser.add_argument("--period-days", type=float, default=7.0)
    parser.add_argument("--exclude", default="", help="ISO start,end window to drop (the Opus anomaly)")
    args = parser.parse_args(argv)

    period = args.period_days * 24 * _HOUR
    resets = parse_resets(args.resets)
    exclude = None
    if args.exclude:
        lo, hi = (parse_resets(args.exclude) + [None, None])[:2]
        exclude = (lo, hi)
    roots = _walker_root_list()

    windows = list(iter_windows(resets, period, exclude))
    if not windows:
        print("no usable windows (need >=2 adjacent resets ~one period apart)")
        return 1

    # Accumulate per-candidate metrics across windows (mean), counting only
    # windows that had spend to normalize.
    agg = {c["label"]: {"convergence_mae_h": 0.0, "jumpiness_h": 0.0, "false_calls": 0, "windows": 0} for c in _CANDIDATES}
    skipped = 0
    for win_start, win_end in windows:
        hourly = reconstruct_window(roots, win_start, win_end)
        util_series = normalize_to_full(hourly)
        if not util_series:
            skipped += 1
            continue
        for candidate in _CANDIDATES:
            params = {k: candidate[k] for k in ("estimator", "window_hours", "lambda", "warmup_seconds")}
            m = score_candidate(hourly, util_series, period, params)
            a = agg[candidate["label"]]
            a["convergence_mae_h"] += m["convergence_mae_h"]
            a["jumpiness_h"] += m["jumpiness_h"]
            a["false_calls"] += m["false_calls"]
            a["windows"] += 1

    rows = []
    for candidate in _CANDIDATES:
        a = agg[candidate["label"]]
        w = max(1, a["windows"])
        rows.append({
            "label": candidate["label"],
            "convergence_mae_h": a["convergence_mae_h"] / w,
            "jumpiness_h": a["jumpiness_h"] / w,
            "false_calls": a["false_calls"],
            "windows": a["windows"],
        })
    rows.sort(key=lambda r: (r["convergence_mae_h"], r["jumpiness_h"], r["false_calls"]))

    if skipped:
        print(f"# note: skipped {skipped} window(s) with no spend to normalize")
    print("# CAVEAT: util is synthetic (dollar-normalized to 100%); rankings are")
    print("# relative-only. Reset boundaries are user-supplied; anomaly excluded.")
    print(_format_table(rows))
    print(f"\n# recommended default: {rows[0]['label']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/verify_backtest_pace.py`
Expected: PASS — `OK: backtest reconstruct + normalize + score produce sane, finite metrics`

- [ ] **Step 5: Run the harness against real data**

Ask the user for their known reset timestamps (they have them — the weekly reset time, plus the anomalous Opus-4.8 Thursday reset to exclude). Then run, e.g.:
```
python scripts/backtest_pace.py --resets "2026-05-08T07:00:00Z,2026-05-15T07:00:00Z,2026-05-22T07:00:00Z,2026-05-29T07:00:00Z" --exclude "2026-05-15T07:00:00Z,2026-05-22T07:00:00Z"
```
Expected: a ranked table. Record the recommended default.

- [ ] **Step 6: Set the empirically-chosen default**

Update `DEFAULT_PARAMS` in `statusline_lib/project.py` to the winning candidate's `estimator` / `window_hours` / `lambda` / `warmup_seconds`. Re-run `python scripts/verify_pace_projection.py` and `python scripts/verify_quota_render.py` (Expected: PASS — the tests are written to be estimator-agnostic).

- [ ] **Step 7: Final gates + commit**

Run: `ruff check . && ruff format --check . && aislop scan . && for t in scripts/verify_*.py; do python "$t" || break; done`
Expected: ruff 0, aislop clean, all verify scripts `OK`.

```bash
git add scripts/backtest_pace.py scripts/verify_backtest_pace.py statusline_lib/project.py
git commit -m "feat(backtest): CLI table over historical windows; set empirical default estimator"
```

---

## Self-Review notes (resolved)

- **Spec coverage:** dual signals (Task 3), window-local hourly walk (Task 2), three estimators + prior (Task 1), arrow display + verbose mode (Task 3), dead-code removal (Task 4), full backtest harness with reconstruction/normalization/scoring/CLI (Tasks 5–6), synthetic-util + broken-cadence caveats (printed by `main`, Task 6). 5h window left unchanged (Task 3 `use_trailing=False` branch).
- **Type consistency:** `project_delta(hourly_burn, util, elapsed, remaining, period_seconds, params) -> (cumulative_delta, current_rate_delta)` used identically in `pace.py` and `backtest_pace.py`. `current_rate(hourly_burn, params)`. Estimator keys `trailing_hours`/`ewma`/`recent_slope` consistent across `_ESTIMATORS`, `DEFAULT_PARAMS`, and `_CANDIDATES`. Clock seam `_now_unix()` introduced in Task 2 and used by Task 3's `_project_pace`.
- **Windows glyph risk:** `↑/↓` flagged in Task 4 Step 4 smoke test (CLAUDE.md stdout-encoding gotcha) with the `sys.stdout.reconfigure` remedy.
```
