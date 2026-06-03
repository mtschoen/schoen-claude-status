"""Line-2 diffstat field: session lines added/removed.

Sourced from the payload's `cost.total_lines_added` / `total_lines_removed`
(session-cumulative). A non-dollar productivity signal, so it deliberately
survives STATUSLINE_HIDE_COST - it is gated only by its own compact-drop flag,
never by the money switch.

Leaf module: base colors + fmt only.
"""

from .base import GREEN, RED, RESET, fmt


def format_lines(added, removed):
    """`+A/-B` (green added, red removed), or "" when nothing has changed yet.

    Counts run through the shared fmt() thousands-compaction so a big refactor
    stays narrow (`+1.2K/-340`). Both halves render whenever either is nonzero,
    so a pure-deletion pass still reads `+0/-340`.
    """
    added = int(added or 0)
    removed = int(removed or 0)
    if added <= 0 and removed <= 0:
        return ""
    return f"{GREEN}+{fmt(added)}{RESET}/{RED}-{fmt(removed)}{RESET}"
