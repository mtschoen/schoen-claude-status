"""Wire statusLine + subagentStatusLine into ~/.claude/settings.json.

Idempotent: re-running just refreshes the two `command` strings; every other
key in settings.json is preserved verbatim. Both entries are written together
because the two scripts are paired -- the lead and per-agent renderings share
formatting code, so installing one without the other gives a mismatched UI.

Usage (typically via the install.sh / install.bat wrappers):
    python install.py --repo /abs/path/to/repo [--settings PATH] [--dry-run]
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
        raise ValueError(f"{path} is not a JSON object (top-level type: {type(data).__name__})")
    return data


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="Absolute path to the schoen-claude-status checkout")
    parser.add_argument("--settings", default=None, help="Path to settings.json (default ~/.claude/settings.json)")
    parser.add_argument("--dry-run", action="store_true", help="Print the merged JSON and exit without writing")
    args = parser.parse_args()

    # Forward slashes -- bash on Windows (Git Bash, MSYS) handles them and the
    # JSON value stays readable across platforms.
    repo = os.path.abspath(args.repo).replace("\\", "/")
    settings_path = args.settings or os.path.expanduser("~/.claude/settings.json")

    main_script = f"{repo}/statusline-command.sh"
    subagent_script = f"{repo}/subagent-statusline.sh"
    for script in (main_script, subagent_script):
        if not os.path.exists(script):
            print(f"error: expected file not found: {script}", file=sys.stderr)
            print("  (is --repo pointing at a complete checkout?)", file=sys.stderr)
            return 1

    main_command = f'bash "{main_script}"'
    subagent_command = f'bash "{subagent_script}"'

    try:
        settings = _load(settings_path)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"error: could not parse {settings_path}: {e}", file=sys.stderr)
        print("  refusing to overwrite a malformed settings file -- fix or move it first", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"error: could not read {settings_path}: {e}", file=sys.stderr)
        return 1

    settings["statusLine"] = {"type": "command", "command": main_command}
    settings["subagentStatusLine"] = {"type": "command", "command": subagent_command}

    if args.dry_run:
        print(f"# would write to {settings_path}")
        print(json.dumps(settings, indent=2))
        return 0

    _atomic_write(settings_path, settings)
    print(f"updated {settings_path}")
    print(f"  statusLine:         {main_command}")
    print(f"  subagentStatusLine: {subagent_command}")
    print("Open a new Claude Code session (or trigger a render) to pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
