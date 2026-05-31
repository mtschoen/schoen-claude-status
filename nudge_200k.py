"""UserPromptSubmit hook: a one-shot nudge to consider ``/wrap`` once a session
crosses the 200K-token context boundary (the Opus 1M-tier 2x pricing line).

Reads the per-session occupancy file that ``statusline.py`` writes -- the
UserPromptSubmit payload itself can't see ``context_window`` -- so it needs no
transcript walk. Fires at most once per session via a marker file, and emits
its text as UserPromptSubmit ``additionalContext``. On any error it stays silent
and exits 0 so it can never block a prompt.

See ``statusline_lib/nudge.py`` for the shared state-file contract, and the
README ("200K-token /wrap nudge") for install and rationale.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from statusline_lib.nudge import (
    NUDGE_MESSAGE,
    read_ctx_used,
    should_nudge,
    write_marker,
)


def _emit(context_text):
    """Print the UserPromptSubmit additionalContext envelope to stdout."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context_text,
            }
        },
        sys.stdout,
    )


def run(stdin_text):
    """Core decision, separated from I/O for testing. Returns the context string
    to emit, or None to stay silent."""
    try:
        payload = json.loads(stdin_text or "{}")
    except ValueError:
        return None
    session_id = payload.get("session_id") or ""
    if not session_id:
        return None
    if not should_nudge(read_ctx_used(session_id), session_id):
        return None
    write_marker(session_id)
    return NUDGE_MESSAGE


def main():
    context_text = run(sys.stdin.read())
    if context_text:
        _emit(context_text)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A hook must never break prompt submission: swallow anything unexpected
        # (e.g. a state-dir race) and exit 0 so the prompt proceeds normally.
        sys.exit(0)
