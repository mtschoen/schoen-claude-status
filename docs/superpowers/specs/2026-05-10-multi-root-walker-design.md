# Multi-root walker — design spec

**Date:** 2026-05-10
**Status:** Spec — working draft, distilled into the plan header and deleted on plan handoff.
**Affects:** `~/claude-walker` (native C++ binary + SPEC.md + conformance), `~/schoen-claude-status` (`statusline_lib.py` fallback path).

## Problem

The trailing-7d sliding-window pace projection in the statusline (`wk: 67% +3.2h` line) reads transcripts under `~/.claude/projects` and projects forward to the rate-limit reset. The same Claude subscription is used from two machines — chonkers (Windows) and llamabox (Linux). Each machine only sees its own transcripts, so the projection systematically under-counts.

On chonkers, the SMB mount at `Y:\` happens to expose llamabox's home directory; chonkers reads both machines' transcripts incidentally because `Y:\.claude\projects` exists. On llamabox there is no equivalent mount in the other direction — llamabox is blind to chonkers' usage.

Out of band, the user will set up a reverse SMB mount of chonkers' `C:\Users\mtsch` at e.g. `/mnt/chonkers` on llamabox. That gives the file system layer access to both transcript trees on both machines. This spec is the walker-side change that lets the walker discover and walk a configurable set of project roots so the projection sees the full subscription view.

## Non-goals

- Transport / sync: not in scope. User sets up the OS-level mount.
- `cost-estimator` skill at `~/.claude/skills/cost-estimator`: separate retrospective-spend tool. A multi-root upgrade there is a follow-up if the user wants per-machine spend breakdowns.
- Cross-host dedup: skipped. Session UUIDs don't collide between hosts and the realpath dedup on the root list handles the only realistic double-count vector (a root listed twice in different forms).

## Architecture

The native `claude-walker` binary learns to walk a **set** of project roots instead of a single one. The default root (`~/.claude/projects`) is always walked. Extra roots come from a per-host config file `~/.claude/walker-roots.json`.

The SPEC.md output shape stays identical — `cost` mode still returns a single `trailing_usd` and `window_usd`. Callers (statusline, conformance harness, future tools) keep their invocations unchanged. All multi-root behavior is contained within the walker.

The Python fallback in `statusline_lib.py:_walk_pace_buckets` mirrors the same config-file read, so the fallback path stays correct when the native binary is absent (e.g. fresh checkout, build broken).

The change touches three subcommands:

- **`cost`** — walks every root, sums into the shared trailing/window buckets. Primary fix.
- **`beacons-history`** — walks every root for bias-factor calibration pairs.
- **`beacons-latest`** — also walks every root looking for the current session's transcript. In practice the local session always wins; no special-casing needed.

## Config schema

File: `~/.claude/walker-roots.json`. Per-host, NOT memory-synced (this is a deployment config, not a user preference).

```json
{
  "extra_roots": [
    "/mnt/chonkers/Users/mtsch/.claude/projects"
  ]
}
```

Single key: `extra_roots`, array of absolute paths.

**Failure modes:**

- File missing → walker behaves exactly as today (single default root).
- File present but malformed JSON → log to stderr, treat as no extras. Never errors out.
- Extra root listed but doesn't exist on disk → skip silently with stderr diagnostic. Important: this is the SMB-mount-unreachable case (chonkers asleep). Walker must not block or error.
- Extra root exists but `fs::directory_iterator` fails mid-walk (permissions, network mount drops) → skip silently with stderr.

None of these cause non-zero exit. The SPEC contract — exit 0 with one JSON line, or fall back to caller's reference — is preserved on every failure mode.

## CLI

New flags on `claude-walker`:

- `--extra-projects-root <path>` — repeatable. Adds to the set discovered via config. For tests, one-offs.
- `--no-config` — skips `walker-roots.json`. Used by the conformance harness for hermetic fixture runs.

Existing `--projects-root <path>` keeps its current meaning: replaces the default root, doesn't replace the extras. So `--projects-root /tmp/fixtures --no-config` is the fully-isolated test invocation.

**Effective root set** at walk time:

```
[--projects-root or ~/.claude/projects]
  + extras from --extra-projects-root flags
  + extras from walker-roots.json  (unless --no-config)
  - deduplicated by std::filesystem::canonical (a.k.a. realpath)
  - filtered: existing directories only
```

## Discovery & walking (C++)

`discover_groups` today takes one `root` and returns a `GroupMap` keyed by `(slug, session_id)`. Change to:

- Accept `const std::vector<fs::path>& roots`.
- Loop over each root, run the existing slug / session / subagent traversal, merge into one `GroupMap`.
- Key stays `(slug, session_id)` — if (hypothetically) the same session ID appears under two roots, those files end up in one group and the existing per-group `seen_ids` set absorbs the collision for free.
- The per-file mtime filter applies as today. Files behind an unreachable mount fail `fs::last_write_time` with an error code and skip cleanly (the existing `if (!ec)` check covers this).

`run_cost`:

- Read config + flags into a `std::vector<fs::path> roots`.
- Pass to `discover_groups`.
- Parallel walk and aggregation are unchanged.

`run_history` (in `beacons.cpp`): same change pattern — accept a vector of roots, walk each.

`run_latest`: pass the root list to its session-JSONL lookup. In practice the local default root always wins, but no harm in checking the extras.

## SPEC.md update

Add a "Roots" section between "CLI contract" and "Discovery" describing the multi-root resolution. Bump the spec to `0.4.0`. The C++ binary's `--version` becomes `cpp/0.4.0`.

## Python fallback

`_walk_pace_buckets` in `statusline_lib.py` currently globs `~/.claude/projects/*/*.jsonl`. Change:

- New private helper `_walker_root_list()` returns `[default_root] + parse_extra_roots()`. The latter reads the same `~/.claude/walker-roots.json` with the same failure-mode contract.
- `_walk_pace_buckets` loops `_walker_root_list()` and merges discovery dicts before dispatching to `_walk_session_group` via the ProcessPoolExecutor.
- `_pace_buckets_cached` cache key extends to include a stable hash of the root list, so the cache invalidates if extras change. Bump the cache filename to `.statusline-pace-cache-v2.json` to avoid loading stale v1 entries.

`format_calibrated_eta` already shells out to `claude-walker beacons-history`; once that subcommand walks extras, no Python changes are needed there. The bias-factor cache file doesn't include root info — TTL is 60s and recompute is cheap, so no key change required.

## Conformance & tests

Add two new fixtures under `~/claude-walker/shared/corpus/`:

- `10-multi-root/` — primary root + a sibling `_extra_root_a/` with its own slug/session, expected output sums both. Invocation uses `--extra-projects-root <fixture>/_extra_root_a` with `--no-config`.
- `11-multi-root-unreachable/` — primary fixture + a non-existent path passed via `--extra-projects-root`. Expected output equals primary-only walk (validates skip-silently semantics).

Conformance harness always invokes with `--no-config` so dev-box `walker-roots.json` doesn't leak into fixture runs.

Beacons-history conformance: add a parallel fixture under `shared/corpus/beacons/` with a remote-extra-root variant to verify cross-root begin/end pairs land in the bias calculation.

## Risks & open questions

- **Mount latency on the hot path.** Statusline renders many times per minute. If the SMB mount stalls (network blip, sleeping host), the walker's `fs::directory_iterator` could hang for the OS-default mount timeout (typically 60-90s on cifs). The mtime filter culls most files but the directory listing itself is the exposure. **Mitigation:** if this turns out to matter in practice, add an mtime check on the root directory itself before recursing, and skip the root if mtime fetch fails or hangs (with a short fs operation timeout). Defer until we see real symptoms.
- **Conformance cross-platform.** Fixtures need to work whether the binary is built on chonkers (MSVC) or llamabox (gcc). Existing fixtures already do, but the multi-root case uses sibling directories — make sure path separators in fixture invocation are normalized.
- **Bias factor calibration sample size.** Adding a second machine's beacons immediately doubles the bias-factor sample pool, which could shift calibration noticeably for users who've been running on one machine. Acceptable — that's the correct direction; the calibration was systematically biased before.

## Out of scope (call-outs)

The `cost-estimator` skill (in `~/.claude/skills/cost-estimator/` per the user's CLAUDE.md) walks JSONLs for retrospective spend analysis. Its multi-root upgrade would follow the same pattern but is a separate piece of work. Flagged so it isn't a surprise when the user notices `/cost estimate` still only reflects one machine after this lands.
