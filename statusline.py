"""Main statusline entry point. Reads Claude Code's JSON payload from stdin
and prints two lines:
  line 1: [host] cwd (branch)
  line 2: ctx | cache | quota | cost  (fields omitted when their data is absent)

See README.md for layout, color thresholds, and install instructions.
"""

import json
import os
import socket
import subprocess
import sys

from statusline_lib import (
    format_cache,
    format_context,
    format_cost,
    format_quota,
    walk_transcript,
)


_INPUT_LOG = os.path.expanduser("~/.claude/.statusline-input.log")


def _safe_write(path, text):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def _hostname():
    try:
        return socket.gethostname().split(".")[0] or "unknown"
    except OSError:
        return "unknown"


def _git_branch(cwd):
    if not cwd:
        return ""
    for arguments in (
        ["git", "-C", cwd, "symbolic-ref", "--short", "HEAD"],
        ["git", "-C", cwd, "rev-parse", "--short", "HEAD"],
    ):
        try:
            out = subprocess.run(
                arguments, capture_output=True, text=True, timeout=2
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def main():
    raw = sys.stdin.read()
    # Truncate-on-write dump of the latest payload. Useful when Claude Code
    # adds new fields we could read directly. Bounded size; cheap.
    _safe_write(_INPUT_LOG, raw)

    try:
        d = json.loads(raw)
    except Exception:
        d = {}

    cwd = (d.get("workspace") or {}).get("current_dir") or d.get("cwd") or ""

    # --- Context: anchored on token counts (avoids the 1% rounding in the
    # payload's used_percentage -- 10K-token slop on a 1M window).
    cw = d.get("context_window") or {}
    window_size = cw.get("context_window_size") or 200_000
    cu = cw.get("current_usage") or {}
    ctx_used = (
        (cu.get("input_tokens") or 0)
        + (cu.get("cache_creation_input_tokens") or 0)
        + (cu.get("cache_read_input_tokens") or 0)
    )
    model_id = (d.get("model") or {}).get("id") or ""
    context_summary = format_context(ctx_used, window_size, model_id)

    # --- Cache: stdin only carries the current turn, so walk the session
    # transcript + every subagent JSONL to sum across all assistant turns.
    walk = walk_transcript(d.get("transcript_path") or "", include_subagents=True)
    cache_summary = format_cache(walk["read"], walk["write"], walk["input"])

    # --- Cost: payload is authoritative (matches /usage, includes subagents).
    cost_summary = format_cost((d.get("cost") or {}).get("total_cost_usd") or 0)

    # --- Quota: 5h + weekly utilization with pace projection.
    quota_summary = format_quota(d.get("rate_limits"))

    # --- Assemble.
    line1 = f"[{_hostname()}] {cwd}"
    branch = _git_branch(cwd)
    if branch:
        line1 = f"{line1} ({branch})"

    parts = [s for s in (context_summary, cache_summary, quota_summary, cost_summary) if s]
    line2 = " | ".join(parts)

    sys.stdout.write(line1)
    if line2:
        sys.stdout.write("\n" + line2)


if __name__ == "__main__":
    main()
