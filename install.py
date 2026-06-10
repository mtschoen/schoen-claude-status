"""Wire statusLine + subagentStatusLine + the wrap nudge hook into
Claude Code or Qwen Code settings.

Idempotent: re-running just refreshes the `command` strings; every other key in
settings.json -- including any other UserPromptSubmit hooks -- is preserved
verbatim. If all three entries already match what we'd write, it reports
"already current" and exits without touching the file. The two statuslines are
written together because they are paired -- the lead and per-agent renderings
share formatting code, so installing one without the other gives a mismatched
UI. The nudge hook is the consumer of the per-session occupancy file the
statusline produces, so it installs in the same pass.

Platform support:
  --platform claude  (default) Installs to ~/.claude/settings.json
  --platform qwen    Installs to ~/.qwen/settings.json (ui.statusLine only)
  --platform both    Installs to both platforms

Usage (typically via the install.sh / install.bat wrappers):
    python install.py --repo /abs/path/to/repo [--platform claude|qwen|both] [--dry-run]
"""

import argparse
import json
import os
import sys


def _load(path):
    """Return parsed dict from `path`, {} if missing/empty, or raise."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(
            f"{path} is not a JSON object (top-level type: {type(data).__name__})"
        )
    return data


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        required=True,
        help="Absolute path to the schoen-claude-status checkout",
    )
    parser.add_argument(
        "--platform",
        choices=["claude", "qwen", "both"],
        default="claude",
        help="Which CLI to install for (default: claude)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merged JSON and exit without writing",
    )
    return parser.parse_args()


def _commands_for_platform(repo):
    """Return (main_target, subagent_target, main_command, subagent_command)."""
    # On Windows, bare python/python3 resolve to the Microsoft Store alias shim,
    # whose ~750ms per-invocation launch overhead dominated every render. Invoke
    # the python.org build via the `py` launcher directly, skipping BOTH the
    # Store shim AND the bash wrapper -- ~50-90ms faster and far less jittery
    # than `bash statusline-command.sh` (Claude Code wraps the command in
    # `cmd /c` on Windows, so no shell prefix is needed). `py -3` keeps it
    # robust across Python minor upgrades -- no hard-coded interpreter path.
    # On other platforms bash + python3 are already fast, so keep the portable
    # shim (which itself prefers `py` where present -- see statusline-command.sh).
    if os.name == "nt":
        main_target = f"{repo}/statusline.py"
        subagent_target = f"{repo}/subagent_statusline.py"
        return (
            main_target,
            subagent_target,
            f'py -3 "{main_target}"',
            f'py -3 "{subagent_target}"',
        )
    main_target = f"{repo}/statusline-command.sh"
    subagent_target = f"{repo}/subagent-statusline.sh"
    return (
        main_target,
        subagent_target,
        f'bash "{main_target}"',
        f'bash "{subagent_target}"',
    )


def _qwen_command_for_platform(repo):
    """Return (target, command) for Qwen Code statusline."""
    # Qwen Code uses the same platform-aware invocation strategy as Claude Code.
    target = f"{repo}/qwen_statusline.py"
    if os.name == "nt":
        command = f'py -3 "{target}"'
    else:
        command = f'bash "{repo}/qwen-statusline-command.sh"'
    return target, command


# Stable identity stamp for our hook entry, appended to the command string as
# a shell comment (`#` starts a comment in both POSIX sh and PowerShell, the
# two shells Claude Code runs hook commands through, so it never affects
# execution). The installer recognizes its own entry by this sentinel rather
# than by the script's filename, so the script can be renamed or moved freely
# without orphaning entries written by earlier installs (which is what
# happened with the nudge_200k.py -> wrap_nudge.py rename). Treat the sentinel
# text itself as frozen: changing it recreates the very problem it solves.
_NUDGE_SENTINEL = "#managed-by:schoen-claude-status/wrap-nudge"


def _nudge_command(repo):
    """Shell-aware command for the UserPromptSubmit wrap nudge hook.

    Wrapped so the hook can never exit 2 -- the one code that BLOCKS prompt
    submission. A missing target or interpreter error has its stderr appended to
    ~/.claude/wrap_nudge_hook.log and the command forces a 0 exit, so a broken
    hook degrades to a log line, never a wedged prompt (and never an error
    injected into Claude's context).

    Claude Code runs hook commands through a shell: PowerShell on Windows (NOT
    cmd.exe -- so %VAR% / ver>nul / || are wrong there; verified empirically by
    the stray-`nul`-file artifact), POSIX sh elsewhere. `$HOME` resolves in both
    PowerShell and sh. `; exit 0` forces non-blocking under PowerShell (works in
    pwsh 7 and Windows PowerShell 5.1, neither of which needs `||`)."""
    target = f"{repo}/wrap_nudge.py"
    if os.name == "nt":
        command = f'py -3 "{target}" 2>>"$HOME\\.claude\\wrap_nudge_hook.log"; exit 0 {_NUDGE_SENTINEL}'
    else:
        command = f'python3 "{target}" 2>>"$HOME/.claude/wrap_nudge_hook.log" || true {_NUDGE_SENTINEL}'
    return target, command


def _nudge_markers(target):
    """Substrings that identify our hook entry among any other UserPromptSubmit
    hooks the user has configured. The sentinel is the durable identity; the
    current script basename is matched as well so an entry written by an
    install that predates the sentinel is migrated in place instead of
    duplicated. The basename is derived fresh from `target` on every run --
    deliberately NOT a maintained list of historical filenames."""
    return (_NUDGE_SENTINEL, os.path.basename(target))


def _find_nudge_hooks(settings, markers):
    """Return every (group, hook) pair recognized as our nudge entry, in
    registration order."""
    found = []
    for group in (settings.get("hooks") or {}).get("UserPromptSubmit") or []:
        for hook in group.get("hooks") or []:
            hook_command = hook.get("command") or ""
            if any(marker in hook_command for marker in markers):
                found.append((group, hook))
    return found


def _nudge_hook_current(settings, markers, command):
    """True iff exactly one nudge hook is present (no stale leftovers from an
    older install) and it already has exactly `command`."""
    matches = _find_nudge_hooks(settings, markers)
    return len(matches) == 1 and matches[0][1].get("command") == command


def _merge_nudge_hook(settings, markers, command):
    """Insert or update the nudge hook, preserving every other hook entry.
    Updates the first match in place, removes any further matches (stale
    entries from an older install, or accidental duplicates), and drops
    matcher groups that removal left empty."""
    matches = _find_nudge_hooks(settings, markers)
    if not matches:
        groups = settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
        groups.append({"hooks": [{"type": "command", "command": command}]})
        return
    matches[0][1]["type"] = "command"
    matches[0][1]["command"] = command
    for group, hook in matches[1:]:
        group["hooks"].remove(hook)
    emptied_ids = {id(group) for group, _ in matches[1:] if not group.get("hooks")}
    if emptied_ids:
        groups = settings["hooks"]["UserPromptSubmit"]
        groups[:] = [group for group in groups if id(group) not in emptied_ids]


def _report_walker(repo):
    # Optional native pace-walker (claude-walker). Pure speedup -- the Python
    # fallback runs identically when it isn't found.
    sys.path.insert(0, repo)
    try:
        from statusline_lib import _find_walker_binary

        walker = _find_walker_binary()
    except ImportError:
        walker = None
    if walker:
        print(f"  walker (native):    {walker}")
    else:
        print("  walker (native):    not found -- using Python fallback")
        print(
            "                      build ~/claude-walker/cpp or set CLAUDE_WALKER_BIN to enable"
        )


def _qwen_settings_current(settings, command):
    """True iff Qwen ui.statusLine already matches `command`."""
    ui = settings.get("ui") or {}
    status_line = ui.get("statusLine") or {}
    return (
        status_line.get("type") == "command" and status_line.get("command") == command
    )


def _merge_qwen_statusline(settings, command):
    """Insert or update ui.statusLine, preserving other ui keys."""
    ui = settings.setdefault("ui", {})
    ui["statusLine"] = {"type": "command", "command": command}


def main():
    args = _parse_args()
    platform = args.platform

    # Forward slashes -- bash on Windows (Git Bash, MSYS) handles them and the
    # JSON value stays readable across platforms.
    repo = os.path.abspath(args.repo).replace("\\", "/")

    install_claude = platform in ("claude", "both")
    install_qwen = platform in ("qwen", "both")

    if install_claude:
        result = _install_claude(repo, args.dry_run)
        if result != 0:
            return result

    if install_qwen:
        result = _install_qwen(repo, args.dry_run)
        if result != 0:
            return result

    return 0


def _install_claude(repo, dry_run):
    """Install statusLine + subagentStatusLine + nudge hook for Claude Code."""
    settings_path = os.path.expanduser("~/.claude/settings.json")

    main_target, subagent_target, main_command, subagent_command = (
        _commands_for_platform(repo)
    )
    nudge_target, nudge_command = _nudge_command(repo)
    nudge_markers = _nudge_markers(nudge_target)

    for script in (main_target, subagent_target, nudge_target):
        if not os.path.exists(script):
            print(f"error: expected file not found: {script}", file=sys.stderr)
            print("  (is --repo pointing at a complete checkout?)", file=sys.stderr)
            return 1

    try:
        settings = _load(settings_path)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"error: could not parse {settings_path}: {e}", file=sys.stderr)
        print(
            "  refusing to overwrite a malformed settings file -- fix or move it first",
            file=sys.stderr,
        )
        return 1
    except OSError as e:
        # Unreadable settings file: report the cause and abort rather than clobber it.
        print(f"error: could not read {settings_path}: {e}", file=sys.stderr)
        return 1

    desired_statusline = {"type": "command", "command": main_command}
    desired_subagent = {"type": "command", "command": subagent_command}

    already_current = (
        settings.get("statusLine") == desired_statusline
        and settings.get("subagentStatusLine") == desired_subagent
        and _nudge_hook_current(settings, nudge_markers, nudge_command)
    )

    if already_current:
        if dry_run:
            print(f"# {settings_path} already current -- nothing to write")
        else:
            print(f"already current: {settings_path}")
            print(f"  statusLine:         {main_command}")
            print(f"  subagentStatusLine: {subagent_command}")
            print(f"  UserPromptSubmit:   {nudge_command}")
            print("Nothing to do.")
        return 0

    settings["statusLine"] = desired_statusline
    settings["subagentStatusLine"] = desired_subagent
    _merge_nudge_hook(settings, nudge_markers, nudge_command)

    if dry_run:
        print(f"# would write to {settings_path}")
        print(json.dumps(settings, indent=2))
        return 0

    _atomic_write(settings_path, settings)
    print(f"updated {settings_path}")
    print(f"  statusLine:         {main_command}")
    print(f"  subagentStatusLine: {subagent_command}")
    print(f"  UserPromptSubmit:   {nudge_command}")

    _report_walker(repo)

    print("Open a new Claude Code session (or trigger a render) to pick it up.")
    return 0


def _install_qwen(repo, dry_run):
    """Install ui.statusLine for Qwen Code."""
    settings_path = os.path.expanduser("~/.qwen/settings.json")

    qwen_target, qwen_command = _qwen_command_for_platform(repo)

    if not os.path.exists(qwen_target):
        print(f"error: expected file not found: {qwen_target}", file=sys.stderr)
        print("  (is --repo pointing at a complete checkout?)", file=sys.stderr)
        return 1

    try:
        settings = _load(settings_path)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"error: could not parse {settings_path}: {e}", file=sys.stderr)
        print(
            "  refusing to overwrite a malformed settings file -- fix or move it first",
            file=sys.stderr,
        )
        return 1
    except OSError as e:
        # Unreadable settings file: report the cause and abort rather than clobber it.
        print(f"error: could not read {settings_path}: {e}", file=sys.stderr)
        return 1

    already_current = _qwen_settings_current(settings, qwen_command)

    if already_current:
        if dry_run:
            print(f"# {settings_path} already current -- nothing to write")
        else:
            print(f"already current: {settings_path}")
            print(f"  ui.statusLine:      {qwen_command}")
            print("Nothing to do.")
        return 0

    _merge_qwen_statusline(settings, qwen_command)

    if dry_run:
        print(f"# would write to {settings_path}")
        print(json.dumps(settings, indent=2))
        return 0

    _atomic_write(settings_path, settings)
    print(f"updated {settings_path}")
    print(f"  ui.statusLine:      {qwen_command}")

    print("Open a new Qwen Code session (or trigger a render) to pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
