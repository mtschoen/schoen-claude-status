"""Validate the repo's SVG asset(s): well-formed XML + structural sanity.

Doubles as the CI gate (the test job globs `scripts/verify_*.py`) and the
go-to local validator whenever `docs/example.svg` is hand-edited. Stdlib only -
no svglint / xmllint / node toolchain, matching the repo's no-extra-deps ethos.

For each `*.svg` under the repo it checks:
  - no DTD / entity declaration (`<!DOCTYPE` / `<!ENTITY`) - a docs SVG never
    needs one, and refusing them keeps the stdlib parser free of any
    entity-expansion (XXE / billion-laughs) surface without pulling in defusedxml
  - well-formed XML (the real hand-edit failure: an unclosed <tspan> or a bare
    `&` that isn't an entity)
  - the root element is <svg> and carries viewBox / width / height
  - no U+2014 em-dash (repo style is ASCII punctuation only)

Run from anywhere: `python scripts/verify_example_svg.py`.
"""

import os
import sys
import xml.etree.ElementTree as ET

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG_NS = "http://www.w3.org/2000/svg"
EM_DASH = "—"


def _svg_files():
    """Every .svg under the repo (currently just docs/example.svg)."""
    found = []
    for dirpath, _dirs, names in os.walk(REPO_ROOT):
        if ".git" in dirpath.split(os.sep):
            continue
        found += [
            os.path.join(dirpath, name) for name in names if name.endswith(".svg")
        ]
    return sorted(found)


def _check_svg(path, failures):
    rel = os.path.relpath(path, REPO_ROOT)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        failures.append(f"{rel}: unreadable ({exc})")
        return
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        failures.append(
            f"{rel}: carries a DTD/entity declaration; SVG assets must not "
            "(avoids entity-expansion risk in the stdlib parser)"
        )
        return
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        failures.append(f"{rel}: not well-formed XML ({exc})")
        return
    if root.tag != f"{{{SVG_NS}}}svg":
        failures.append(f"{rel}: root element is {root.tag!r}, expected <svg>")
    for attr in ("viewBox", "width", "height"):
        if root.get(attr) is None:
            failures.append(f"{rel}: <svg> missing required attribute {attr!r}")
    if EM_DASH in text:
        failures.append(f"{rel}: contains a U+2014 em-dash; use ASCII punctuation")


def check(failures):
    files = _svg_files()
    if not files:
        failures.append("no .svg files found under the repo")
        return
    for path in files:
        _check_svg(path, failures)


def main():
    failures = []
    check(failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("OK: all repo SVGs are well-formed and structurally valid")


if __name__ == "__main__":
    main()
