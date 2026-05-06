"""Shared rendering helpers for the schoen-claude-status statuslines.

Used by:
  statusline.py            -- main session: 2-line layout
  subagent_statusline.py   -- per-agent panel rows: 1-line metrics

Cost handling differs between the two callers:
  Main script reads `cost.total_cost_usd` from the stdin payload (authoritative,
  matches /usage). Subagent script must derive cost from the agent JSONL since
  the per-task fields don't include cost. The walker computes both in one pass
  so the subagent path doesn't need a second iteration.

Per-Mtok rates and the 1.25x cache-write multiplier match the canonical
constants documented in ~/.claude/CLAUDE.md ("Cost-estimation formula"). The
Opus 1M-context tier doubling (>200K context) is intentionally NOT modeled --
matches the main-script's accepted ~5-10% under-estimate for big-context Opus.
"""

import glob
import json
import os
from datetime import datetime, timezone

# --- ANSI colors -----------------------------------------------------------
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
GREEN = "\x1b[32m"
RESET = "\x1b[0m"
# Identity colors (256-color) -- distinct from the threshold band so identity
# never reads as a warning.
CACHE_READ = "\x1b[38;5;38m"     # teal
CACHE_WRITE = "\x1b[38;5;208m"   # orange
CTX_DENOM = "\x1b[38;5;139m"     # soft mauve

# --- Pricing ---------------------------------------------------------------
# (input_per_mtok, output_per_mtok). Cache read = 0.1x input; cache write =
# 1.25x input (matches billing as of 2026-04-30; docs say 2.0x for 1h-TTL,
# empirically wrong).
_RATES = {
    "opus":   (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku":  (1.0,  5.0),
}


def _rates_for(model_id):
    mid = (model_id or "").lower()
    for key, rates in _RATES.items():
        if key in mid:
            return rates
    # Unknown family -- fall back to sonnet rates rather than zero so an
    # unrecognized model doesn't silently render as free.
    return _RATES["sonnet"]


def _cost_for_turn(usage, model_id):
    """Per-Mtok cost for one assistant turn's usage dict."""
    inp_rate, out_rate = _rates_for(model_id)
    i = int(usage.get("input_tokens") or 0)
    r = int(usage.get("cache_read_input_tokens") or 0)
    w = int(usage.get("cache_creation_input_tokens") or 0)
    o = int(usage.get("output_tokens") or 0)
    return (
        i * inp_rate
        + r * (inp_rate * 0.1)
        + w * (inp_rate * 1.25)
        + o * out_rate
    ) / 1_000_000.0


# --- Number/percentage formatting -----------------------------------------
def fmt(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1000:.1f}K"
    return str(int(n))


def color_high_bad(pct, warn, danger, decimals=0):
    """Higher is worse (e.g. ctx %, quota %). >= warn -> yellow, >= danger -> red."""
    c = RED if pct >= danger else YELLOW if pct >= warn else GREEN
    spec = f".{decimals}f"
    return f"{c}{format(pct, spec)}%{RESET}"


def color_high_good(pct, warn, danger, decimals=0):
    """Higher is better (e.g. cache hit %). < warn -> yellow, < danger -> red."""
    c = RED if pct < danger else YELLOW if pct < warn else GREEN
    spec = f".{decimals}f"
    return f"{c}{format(pct, spec)}%{RESET}"


# --- Transcript walker ----------------------------------------------------
def walk_transcript(path, include_subagents=False):
    """Sum cache/input/output tokens, compute cost, snapshot most-recent turn.

    Returns:
      cache_read, cache_write, input_total, output_total -- session sums
      cost                                               -- $, derived
      last_model_id                                      -- model on most recent assistant turn
      last_input, last_cache_create, last_cache_read     -- usage of most recent turn
                                                            (used to derive ctx_used at "now")

    `include_subagents=True` (main script) also walks
    <path-without-.jsonl>/subagents/agent-*.jsonl so the cache total reflects
    everything attributed to this session. The subagent script passes False.
    """
    read_total = write_total = input_total = output_total = 0
    cost_total = 0.0
    last_model = ""
    last_input = last_cache_create = last_cache_read = 0
    seen_ids = set()

    def process(p):
        nonlocal read_total, write_total, input_total, output_total
        nonlocal cost_total, last_model, last_input, last_cache_create, last_cache_read
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    msg = e.get("message") or {}
                    if msg.get("role") != "assistant":
                        continue
                    mid = msg.get("id")
                    if mid:
                        # transcripts repeat assistant turns under one message.id
                        # (snapshots/checkpoints carry the same usage); count once.
                        if mid in seen_ids:
                            continue
                        seen_ids.add(mid)
                    u = msg.get("usage") or {}
                    r = int(u.get("cache_read_input_tokens") or 0)
                    w = int(u.get("cache_creation_input_tokens") or 0)
                    i = int(u.get("input_tokens") or 0)
                    o = int(u.get("output_tokens") or 0)
                    read_total += r
                    write_total += w
                    input_total += i
                    output_total += o
                    model_id = msg.get("model") or ""
                    if model_id:
                        last_model = model_id
                    cost_total += _cost_for_turn(u, model_id or last_model)
                    last_input = i
                    last_cache_create = w
                    last_cache_read = r
        except OSError:
            pass

    if path and os.path.exists(path):
        process(path)
        if include_subagents and path.endswith(".jsonl"):
            sub_dir = path[:-6] + "/subagents"
            if os.path.isdir(sub_dir):
                for sub in glob.glob(os.path.join(sub_dir, "agent-*.jsonl")):
                    process(sub)

    return {
        "read": read_total,
        "write": write_total,
        "input": input_total,
        "output": output_total,
        "cost": cost_total,
        "last_model_id": last_model,
        "last_input": last_input,
        "last_cache_create": last_cache_create,
        "last_cache_read": last_cache_read,
    }


# --- Field formatters -----------------------------------------------------
COMPACT_BUFFER_TOKENS = 33_000
RED_MARGIN_TOKENS = 20_000


def ctx_window_for_model(model_id):
    """Best-effort window inference for per-agent rendering. Opus [1m] -> 1M,
    everything else -> 200K. The main script doesn't need this -- the payload
    carries `context_window.context_window_size` directly."""
    return 1_000_000 if "[1m]" in (model_id or "") else 200_000


def format_context(ctx_used, window_size, model_id=""):
    """`usedK / windowK (P.P%)` colored by token-anchored thresholds.

    Yellow at 200K for 1M models (Opus 1M pricing boundary), at 50% otherwise.
    Red at `window_size - 33K compact buffer - 20K headroom`; tracks
    CLAUDE_AUTOCOMPACT_PCT_OVERRIDE if set.
    """
    if window_size <= 0:
        return ""
    override = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE")
    compact_tokens = max(0, window_size - COMPACT_BUFFER_TOKENS)
    if override:
        try:
            compact_tokens = int(window_size * float(override) / 100)
        except ValueError:
            pass
    red_tokens = max(0, compact_tokens - RED_MARGIN_TOKENS)
    is_1m = window_size >= 1_000_000 or "[1m]" in (model_id or "")
    yellow_tokens = 200_000 if is_1m else window_size // 2
    if ctx_used >= red_tokens:
        ctx_color = RED
    elif ctx_used >= yellow_tokens:
        ctx_color = YELLOW
    else:
        ctx_color = GREEN
    pct = 100.0 * ctx_used / window_size
    return (
        f"{ctx_color}{fmt(ctx_used)}{RESET} / "
        f"{CTX_DENOM}{fmt(window_size)}{RESET} "
        f"({ctx_color}{pct:.1f}%{RESET})"
    )


def format_cache(read, write, input_t):
    total_in = read + write + input_t
    if total_in <= 0:
        return ""
    hit_pct = read * 100.0 / total_in
    return (
        f"{CACHE_READ}{fmt(read)}{RESET} / "
        f"{CACHE_WRITE}{fmt(write)}{RESET} / "
        f"{color_high_good(hit_pct, 90, 75)} hit"
    )


def format_cost(cost):
    if cost is None or cost <= 0:
        return ""
    color = RED if cost >= 50 else YELLOW if cost >= 25 else GREEN
    return f"{color}${cost:.2f}{RESET}"


# --- Quota (main script only) --------------------------------------------
def _fmt_delta_hours(seconds):
    sign = "+" if seconds >= 0 else "-"
    return f"{sign}{abs(seconds) / 3600:.1f}h"


def _project_pace(util, resets_at_unix, period_seconds):
    """Returns ' +X.Yh' (colored) or '' if not enough data."""
    if util is None or util <= 0 or not resets_at_unix:
        return ""
    try:
        reset_dt = datetime.fromtimestamp(resets_at_unix, tz=timezone.utc)
        remaining = (reset_dt - datetime.now(timezone.utc)).total_seconds()
        elapsed = period_seconds - remaining
        if elapsed <= 0 or remaining <= 0:
            return ""
        delta = 100.0 * elapsed / util - period_seconds
        warn_threshold = 0.05 * period_seconds
        if delta < 0:
            color = RED
        elif delta <= warn_threshold:
            color = YELLOW
        else:
            color = GREEN
        return f" {color}{_fmt_delta_hours(delta)}{RESET}"
    except Exception:
        return ""


def format_quota(rate_limits):
    """Returns space-joined '5h: P% +Hh wk: P% +Hh', omitting unavailable windows."""
    rl = rate_limits or {}
    parts = []
    for win_key, period_seconds, label in (
        ("five_hour", 5 * 3600, "5h"),
        ("seven_day", 7 * 86400, "wk"),
    ):
        w = rl.get(win_key) or {}
        util = w.get("used_percentage")
        if util is None:
            continue
        pct_part = color_high_bad(util, 75, 90)
        proj_part = _project_pace(util, w.get("resets_at"), period_seconds)
        parts.append(f"{label}: {pct_part}{proj_part}")
    return " ".join(parts)
