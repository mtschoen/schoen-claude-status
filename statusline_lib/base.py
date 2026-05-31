"""Base constants and helpers used by all other statusline_lib modules.

No imports from sibling modules — keeps the dependency order clean.
"""

import json

# orjson: optional, ~3-5x faster per-line parse; stdlib json fallback.
try:
    from orjson import loads as _json_loads
except ImportError:
    _json_loads = json.loads

RED = "\x1b[31m"
YELLOW = "\x1b[33m"
ORANGE = "\x1b[38;5;208m"  # mid-tier between yellow and red
GREEN = "\x1b[32m"
RESET = "\x1b[0m"
# Identity colors (256-color) -- distinct from the threshold band so identity
# never reads as a warning.
CACHE_READ = "\x1b[38;5;38m"  # teal
CACHE_WRITE = ORANGE  # cache-write identity reuses the orange hue
CTX_DENOM = "\x1b[38;5;139m"  # soft mauve


def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1000:.1f}K"
    return str(int(n))


def color_high_bad(pct, warn, danger, decimals=0):
    """Higher is worse (e.g. quota %, day %). Smooth gradient: solid green at/below
    warn, ramps through yellow to red by danger (pass warn < danger)."""
    spec = f".{decimals}f"
    return f"{ramp_color_for(pct, warn, danger)}{format(pct, spec)}%{RESET}"


def color_high_good(pct, warn, danger, decimals=0):
    """Higher is better (e.g. cache hit %). Smooth gradient: solid green at/above
    warn, ramps through yellow to red by danger (pass warn > danger, e.g. 90, 75)."""
    spec = f".{decimals}f"
    return f"{ramp_color_for(pct, warn, danger)}{format(pct, spec)}%{RESET}"


# Green -> yellow -> red 256-color ramp; shared by burn rate, quota %, cache-hit %, pace deltas.
RAMP = [46, 82, 118, 154, 190, 226, 220, 214, 208, 202, 196]


def ramp_color(t):
    """256-color escape on the green(0)->yellow->red(1) ramp for t, clamped to [0,1]."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return f"\x1b[38;5;{RAMP[round(t * (len(RAMP) - 1))]}m"


def ramp_color_for(value, warn, danger):
    """ramp_color for a threshold-style value: `warn` is the green edge, `danger`
    the red edge. Solid green at/beyond warn (away from danger), ramps through
    yellow to red at danger, solid red beyond. Covers high-bad (warn < danger)
    and high-good (warn > danger) by orientation of the two anchors."""
    if warn == danger:
        return ramp_color(1.0 if value >= warn else 0.0)
    return ramp_color((value - warn) / (danger - warn))
