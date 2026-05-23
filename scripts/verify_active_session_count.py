"""Verify `count_active_sessions` and its classifier `_process_matches`.

Covers two paths:
  - Mtime fallback (no cwd passed): the original slug-dir scan.
  - psutil classifier: pure-function tests of `_process_matches` with
    synthesized (name, cmdline, cwd) inputs -- no live or mocked psutil
    needed.

Run from anywhere; imports from `schoen-claude-status` by path.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from statusline_lib import count_active_sessions, _process_matches


def touch(path, mtime):
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}\n")
    os.utime(path, (mtime, mtime))


def check_mtime_fallback(failures):
    now = time.time()
    with tempfile.TemporaryDirectory() as slug_dir:
        transcript_path = os.path.join(slug_dir, "self.jsonl")

        # Case 1: empty dir -> 0
        if count_active_sessions(transcript_path, now=now) != 0:
            failures.append("empty dir should return 0")

        # Case 2: two fresh JSONLs -> 2
        touch(os.path.join(slug_dir, "self.jsonl"), now - 5)
        touch(os.path.join(slug_dir, "other.jsonl"), now - 60)
        if count_active_sessions(transcript_path, now=now) != 2:
            failures.append("two fresh JSONLs should return 2")

        # Case 3: stale file is ignored
        touch(os.path.join(slug_dir, "stale.jsonl"), now - 3600)
        if count_active_sessions(transcript_path, now=now) != 2:
            failures.append("stale file should be ignored")

        # Case 4: subagent subdir contents are ignored
        subagent_dir = os.path.join(slug_dir, "agent-uuid")
        os.makedirs(subagent_dir)
        touch(os.path.join(subagent_dir, "agent.jsonl"), now - 5)
        if count_active_sessions(transcript_path, now=now) != 2:
            failures.append("subagent subdir JSONL should be ignored")

        # Case 5: non-jsonl files are ignored
        touch(os.path.join(slug_dir, "scratch.txt"), now - 5)
        if count_active_sessions(transcript_path, now=now) != 2:
            failures.append("non-jsonl file should be ignored")

    # Case 6: missing dir / empty transcript path -> 0 without exception
    if count_active_sessions("", now=now) != 0:
        failures.append("empty transcript_path should return 0")
    if count_active_sessions("/nonexistent/path/foo.jsonl", now=now) != 0:
        failures.append("missing dir should return 0")

    # Case 7: default `now=` exercises the live time.time() branch.
    if count_active_sessions("") != 0:
        failures.append("default now should still return 0 for empty path")


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


def check_psutil_dispatch(failures):
    # When cwd is provided and psutil is available, the function delegates
    # to the psutil path. We can't synthesize fake claude processes here
    # without a heavier mocking setup, but we can verify the dispatch
    # returns 0 cleanly for a cwd that no real claude is running in.
    bogus_cwd = os.path.join(tempfile.gettempdir(), "definitely-not-a-claude-cwd-zzz")
    if count_active_sessions("", cwd=bogus_cwd) != 0:
        failures.append("psutil path with no matching processes should return 0")


def main():
    failures = []
    check_mtime_fallback(failures)
    check_classifier(failures)
    check_psutil_dispatch(failures)

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        sys.exit(1)
    print("OK: count_active_sessions + _process_matches behave correctly across all cases")


if __name__ == "__main__":
    main()
