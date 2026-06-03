"""Verify the two new payload-sourced fields: the line-2 diffstat
(format_lines) and the line-3 session timing (format_session_timing /
_fmt_duration_ms).
"""

import os
import re
import sys

# Failure messages echo the ⏳ glyph; force UTF-8 so a FAIL print doesn't itself
# crash on a cp1252 Windows console.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from statusline_lib.beacon import _fmt_duration_ms, format_session_timing
from statusline_lib.diffstat import format_lines

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip(text):
    return _ANSI.sub("", text)


def _check_lines(failures):
    if _strip(format_lines(543, 113)) != "+543/-113":
        failures.append(f"basic diffstat wrong: {_strip(format_lines(543, 113))!r}")
    # fmt() thousands-compaction on big refactors.
    if _strip(format_lines(1234, 56)) != "+1.2K/-56":
        failures.append(f"compaction wrong: {_strip(format_lines(1234, 56))!r}")
    # Pure-deletion still shows both halves.
    if _strip(format_lines(0, 340)) != "+0/-340":
        failures.append(f"pure deletion wrong: {_strip(format_lines(0, 340))!r}")
    # Nothing changed yet -> empty (drops out of the line).
    if format_lines(0, 0) != "":
        failures.append(f"no-change must be empty: {format_lines(0, 0)!r}")
    if format_lines(None, None) != "":
        failures.append(f"None counts must be empty: {format_lines(None, None)!r}")


def _check_duration(failures):
    cases = {
        500: "",  # < 1s rounds to 0 -> empty
        45_000: "45s",
        720_000: "12m",
        2_895_874: "48m",
        3_600_000: "1h00m",
        4_080_000: "1h08m",
        None: "",
        "x": "",
        0: "",
    }
    for ms, expected in cases.items():
        got = _fmt_duration_ms(ms)
        if got != expected:
            failures.append(
                f"_fmt_duration_ms({ms!r}) = {got!r}, expected {expected!r}"
            )


def _check_timing(failures):
    full = _strip(
        format_session_timing(
            {"total_duration_ms": 2_895_874, "total_api_duration_ms": 1_181_774}
        )
    )
    # api floors: 1_181_774 ms = 19.7 min -> 19m.
    if full != "⏳ 48m · 19m api":
        failures.append(f"full timing wrong: {full!r}")
    # No api figure -> drop the tail, keep wall.
    no_api = _strip(format_session_timing({"total_duration_ms": 720_000}))
    if no_api != "⏳ 12m":
        failures.append(f"wall-only timing wrong: {no_api!r}")
    # No wall figure (brand-new session) -> empty, so line 3 falls back to the
    # beacon alone.
    if format_session_timing({"total_api_duration_ms": 5_000}) != "":
        failures.append("missing wall must yield empty timing")
    if format_session_timing(None) != "":
        failures.append("non-dict cost must yield empty timing")


def main():
    failures = []
    _check_lines(failures)
    _check_duration(failures)
    _check_timing(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("OK: diffstat + session timing render correctly")


if __name__ == "__main__":
    main()
