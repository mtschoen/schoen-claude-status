"""Verify the reusable green->yellow->red gradient helper in base.py.

ramp_color(t) maps t in [0,1] onto a truecolor ramp (green at 0, red at 1),
piecewise-linear between the RGB anchors in RAMP.
ramp_color_for(value, warn, danger) is banded: `warn` is the green edge and
`danger` the red edge, with solid green/red plateaus beyond. It works for both
high-bad (warn < danger) and high-good (warn > danger) orientations.

Run from anywhere; imports from `schoen-claude-status` by path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from statusline_lib.base import (
    RAMP,
    color_high_bad,
    color_high_good,
    ramp_color,
    ramp_color_for,
)


def _expected(rgb):
    r, g, b = rgb
    return f"\x1b[38;2;{r};{g};{b}m"


def _check_endpoints(failures):
    if ramp_color(0.0) != _expected(RAMP[0]):
        failures.append("ramp_color(0.0) should be the green end of the ramp")
    if ramp_color(1.0) != _expected(RAMP[-1]):
        failures.append("ramp_color(1.0) should be the red end of the ramp")
    mid = RAMP[round(0.5 * (len(RAMP) - 1))]
    if ramp_color(0.5) != _expected(mid):
        failures.append("ramp_color(0.5) should be the ramp midpoint")


def _check_clamp(failures):
    if ramp_color(-3.0) != ramp_color(0.0):
        failures.append("ramp_color clamps below 0 to the green end")
    if ramp_color(9.9) != ramp_color(1.0):
        failures.append("ramp_color clamps above 1 to the red end")


def _check_high_bad_mapping(failures):
    # warn=75 (green edge), danger=90 (red edge): at/below warn -> green,
    # danger -> red, midpoint (82.5) -> yellow.
    if ramp_color_for(75, 75, 90) != ramp_color(0.0):
        failures.append("high-bad at warn should be solid green")
    if ramp_color_for(60, 75, 90) != ramp_color(0.0):
        failures.append("high-bad below warn should clamp to green")
    if ramp_color_for(90, 75, 90) != ramp_color(1.0):
        failures.append("high-bad danger should map to the ramp hot end")
    if ramp_color_for(82.5, 75, 90) != ramp_color(0.5):
        failures.append("high-bad midpoint should be yellow")


def _check_high_good_mapping(failures):
    # cache hit %: warn=90 (green edge), danger=75 (red edge). Full green across
    # 90-100, red at 75, midpoint (82.5) -> yellow.
    if ramp_color_for(90, 90, 75) != ramp_color(0.0):
        failures.append("high-good at warn should be solid green")
    if ramp_color_for(100, 90, 75) != ramp_color(0.0):
        failures.append("high-good across 90-100 should stay solid green")
    if ramp_color_for(75, 90, 75) != ramp_color(1.0):
        failures.append("high-good danger should map to the ramp hot end")
    if ramp_color_for(82.5, 90, 75) != ramp_color(0.5):
        failures.append("high-good midpoint should be yellow")


def _check_delta_mapping(failures):
    # pace delta: warn_threshold = green edge, 0 = red edge (higher surplus better).
    wt = 30240.0
    if ramp_color_for(wt, wt, 0) != ramp_color(0.0):
        failures.append("delta at warn_threshold should be solid green")
    if ramp_color_for(2 * wt, wt, 0) != ramp_color(0.0):
        failures.append("delta above warn_threshold should clamp to green")
    if ramp_color_for(0, wt, 0) != ramp_color(1.0):
        failures.append("delta at 0 should be red")
    if ramp_color_for(-100, wt, 0) != ramp_color(1.0):
        failures.append("negative delta should clamp to red")
    if ramp_color_for(wt / 2, wt, 0) != ramp_color(0.5):
        failures.append("delta at half warn_threshold should be yellow")


def _check_threshold_colorizers(failures):
    # color_high_bad(_, 75, 90): solid green at/below 75, red at 90.
    if not color_high_bad(75, 75, 90).startswith(ramp_color(0.0)):
        failures.append("color_high_bad at warn should be solid green")
    if not color_high_bad(90, 75, 90).startswith(ramp_color(1.0)):
        failures.append("color_high_bad should be red at danger")
    if "75%" not in color_high_bad(75, 75, 90):
        failures.append("color_high_bad must still render the percent text")
    # color_high_good(_, 90, 75): full green across 90-100, red at 75.
    if not color_high_good(90, 90, 75).startswith(ramp_color(0.0)):
        failures.append("color_high_good at 90 should be solid green")
    if not color_high_good(98, 90, 75).startswith(ramp_color(0.0)):
        failures.append("color_high_good across 90-100 should stay solid green")
    if not color_high_good(75, 90, 75).startswith(ramp_color(1.0)):
        failures.append("color_high_good should be red at danger")


def check(failures):
    _check_endpoints(failures)
    _check_clamp(failures)
    _check_high_bad_mapping(failures)
    _check_high_good_mapping(failures)
    _check_delta_mapping(failures)
    _check_threshold_colorizers(failures)


def main():
    failures = []
    check(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("OK: ramp_color + ramp_color_for map onto the gradient correctly")


if __name__ == "__main__":
    main()
