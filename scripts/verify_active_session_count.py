"""Verify `count_active_sessions` and its classifier `_process_matches`.

Covers:
  - Dispatch behavior of `count_active_sessions`: returns 0 for empty cwd
    and for a cwd no real claude is running in (psutil path with no match).
  - Pure-function tests of `_process_matches` with synthesized
    (name, cmdline, cwd) inputs -- no live or mocked psutil needed.

Run from anywhere; imports from `schoen-claude-status` by path.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from statusline_lib import count_active_sessions, _process_matches


def check_dispatch(failures):
    # Empty cwd cannot be enumerated against -> 0, no exception.
    if count_active_sessions("") != 0:
        failures.append("empty cwd should return 0")

    # A cwd that no real claude process is running in -> 0.
    bogus_cwd = os.path.join(tempfile.gettempdir(), "definitely-not-a-claude-cwd-zzz")
    if count_active_sessions(bogus_cwd) != 0:
        failures.append("cwd with no matching claude processes should return 0")


def check_classifier(failures):
    target = os.path.normcase("/home/user/proj")

    # Positive cases
    if not _process_matches("claude", ["claude"], "/home/user/proj", target):
        failures.append("interactive claude in target cwd should match")
    if not _process_matches("claude.exe", ["claude.exe"], "/home/user/proj", target):
        failures.append("claude.exe in target cwd should match (Windows)")
    if not _process_matches("node", ["node", "/path/to/claude/cli.js"], "/home/user/proj", target):
        failures.append("node-wrapped claude should match")

    # Negative: -p / --print headless mode (Task subagents, scripted)
    if _process_matches("claude.exe", ["claude.exe", "-p", "--output-format", "json"], "/home/user/proj", target):
        failures.append("-p subagent should NOT match")
    if _process_matches("claude", ["claude", "--print", "hi"], "/home/user/proj", target):
        failures.append("--print subagent should NOT match")

    # Negative: wrong cwd
    if _process_matches("claude", ["claude"], "/other/cwd", target):
        failures.append("wrong cwd should NOT match")

    # Negative: unrelated process name
    if _process_matches("python", ["python", "script.py"], "/home/user/proj", target):
        failures.append("non-claude process should NOT match")

    # Negative: node without 'claude' in argv (regular Node app)
    if _process_matches("node", ["node", "server.js"], "/home/user/proj", target):
        failures.append("node without claude in argv should NOT match")

    # Negative: empty / None cwd
    if _process_matches("claude", ["claude"], "", target):
        failures.append("empty cwd should NOT match")
    if _process_matches("claude", ["claude"], None, target):
        failures.append("None cwd should NOT match")


def main():
    failures = []
    check_dispatch(failures)
    check_classifier(failures)

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        sys.exit(1)
    print("OK: count_active_sessions + _process_matches behave correctly across all cases")


if __name__ == "__main__":
    main()
