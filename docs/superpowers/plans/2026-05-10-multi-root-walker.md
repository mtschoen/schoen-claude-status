# Multi-root walker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach `claude-walker` (native C++ binary) and the Python fallback in `statusline_lib.py` to walk a configurable set of project roots so the sliding-window pace projection sees both machines' transcripts when the same subscription is used from chonkers and llamabox.

**Architecture:** Adds a per-host config file `~/.claude/walker-roots.json` listing extra project roots. The walker (cost mode, beacons-history, beacons-latest) merges the default root with extras at discovery time, deduped via `fs::canonical`, and walks them all under the same grouping/dedup rules. Aggregated output shape is unchanged so callers (statusline, conformance) don't have to know multi-root exists. The Python fallback mirrors the same config-file read.

**Tech Stack:** C++17 + simdjson (`~/claude-walker/cpp/`), Python 3 (`~/schoen-claude-status/statusline_lib.py`), JSON config, existing conformance harness at `~/claude-walker/shared/conformance.py`.

**Cross-repo scope:** The work spans two git repos:
- `C:\Users\mtsch\claude-walker` — native binary, SPEC, conformance, fixtures.
- `C:\Users\mtsch\schoen-claude-status` — Python fallback.

Each phase commits in its own repo. Cross-repo coordination point: Phase 7 verifies the integrated behavior end-to-end.

---

## Phase 1: Config helper (walker)

Build the shared C++ helper that reads `~/.claude/walker-roots.json` and produces a deduped, existence-filtered vector of paths. Used by all three subcommands.

### Task 1.1: Add `walker_roots.hpp` helper

**Files:**
- Create: `C:\Users\mtsch\claude-walker\cpp\walker_roots.hpp`

- [ ] **Step 1: Create the header**

```cpp
// Roots discovery: default root + extras from ~/.claude/walker-roots.json
// + extras from CLI flags. Deduped via fs::canonical, filtered to
// existing directories.
//
// Failure modes follow the SPEC contract:
//   * Missing config file -> no extras (silent).
//   * Malformed JSON -> stderr diagnostic, treat as no extras.
//   * Listed path doesn't exist on disk -> skip silently (stderr).
//   * canonical() fails (broken symlink etc) -> fall back to lexically_normal.

#ifndef WALKER_ROOTS_HPP
#define WALKER_ROOTS_HPP

#include "common.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

#include <simdjson.h>

namespace walker {

namespace fs = std::filesystem;
namespace sj = simdjson;

inline fs::path walker_config_path() {
    const char* home = std::getenv("HOME");
    if (!home) home = std::getenv("USERPROFILE");
    if (home) return fs::path(home) / ".claude" / "walker-roots.json";
    return fs::path(".claude/walker-roots.json");
}

// Parse extras from `~/.claude/walker-roots.json`. Returns empty vector on
// any failure (with a stderr diagnostic for malformed JSON specifically).
inline std::vector<fs::path> read_extra_roots_from_config() {
    fs::path config = walker_config_path();
    std::error_code ec;
    if (!fs::exists(config, ec)) return {};

    std::ifstream in(config);
    if (!in) return {};
    std::ostringstream buf;
    buf << in.rdbuf();
    std::string body = buf.str();
    if (body.empty()) return {};

    sj::ondemand::parser parser;
    sj::padded_string padded(body);
    sj::ondemand::document doc;
    if (parser.iterate(padded).get(doc) != sj::SUCCESS) {
        std::cerr << "walker: malformed " << config.string()
                  << " -- ignoring extra roots\n";
        return {};
    }
    sj::ondemand::object root;
    if (doc.get_object().get(root) != sj::SUCCESS) {
        std::cerr << "walker: " << config.string()
                  << " is not a JSON object -- ignoring\n";
        return {};
    }

    std::vector<fs::path> extras;
    for (auto field : root) {
        std::string_view key;
        if (field.unescaped_key().get(key) != sj::SUCCESS) continue;
        if (key != "extra_roots") continue;

        sj::ondemand::array arr;
        if (field.value().get_array().get(arr) != sj::SUCCESS) continue;

        for (auto element : arr) {
            std::string_view path_view;
            if (element.get_string().get(path_view) != sj::SUCCESS) continue;
            if (path_view.empty()) continue;
            extras.emplace_back(std::string(path_view));
        }
    }
    return extras;
}

// Resolve the effective root list:
//   [primary] + cli_extras + (config extras if read_config)
//   -> dedup via canonical
//   -> filter to existing directories
inline std::vector<fs::path> resolve_roots(
    const fs::path& primary,
    const std::vector<fs::path>& cli_extras,
    bool read_config)
{
    std::vector<fs::path> all;
    all.push_back(primary);
    for (const auto& p : cli_extras) all.push_back(p);
    if (read_config) {
        for (const auto& p : read_extra_roots_from_config()) all.push_back(p);
    }

    std::vector<fs::path> result;
    std::unordered_set<std::string> seen;
    for (const auto& p : all) {
        std::error_code ec;
        if (!fs::exists(p, ec) || !fs::is_directory(p, ec)) {
            if (&p != &all[0]) {  // primary is allowed to not exist; that's the empty-fleet case
                std::cerr << "walker: extra root not a directory, skipping: "
                          << p.string() << "\n";
            }
            continue;
        }
        fs::path canon = fs::canonical(p, ec);
        if (ec) canon = p.lexically_normal();
        std::string key = canon.string();
        if (seen.insert(key).second) {
            result.push_back(canon);
        }
    }
    return result;
}

}  // namespace walker

#endif  // WALKER_ROOTS_HPP
```

- [ ] **Step 2: Verify it compiles**

Run: `cd C:\Users\mtsch\claude-walker\cpp\build && cmake --build . --config Release`
Expected: build succeeds (the header is only included from main.cpp / beacons.cpp later — this step verifies it's syntactically valid by inclusion in a test compile).

Touch a no-op include to force re-link if needed:

```bash
# In a PowerShell window:
echo "#include \"walker_roots.hpp\"" >> C:\Users\mtsch\claude-walker\cpp\common.hpp
cmake --build C:\Users\mtsch\claude-walker\cpp\build --config Release
# Then revert:
git checkout C:\Users\mtsch\claude-walker\cpp\common.hpp
```

- [ ] **Step 3: Commit (in claude-walker repo)**

```bash
cd C:\Users\mtsch\claude-walker
git add cpp/walker_roots.hpp
git commit -m "walker: add walker_roots.hpp config + dedup helper"
```

---

## Phase 2: cost-mode multi-root

Wire the resolver into `run_cost`.

### Task 2.1: Add `--extra-projects-root` and `--no-config` to cost-mode argument parser

**Files:**
- Modify: `C:\Users\mtsch\claude-walker\cpp\main.cpp` (Args struct + parse_args)

- [ ] **Step 1: Update Args struct (replace lines 38-43)**

```cpp
struct Args {
    uint64_t period_seconds = 0;
    double win_start_unix = 0.0;
    std::optional<double> now_unix;
    std::optional<fs::path> projects_root;
    std::vector<fs::path> extra_projects_roots;
    bool read_config = true;
};
```

- [ ] **Step 2: Add flag handling to parse_args (inside the loop at lines 52-75)**

Insert before the final `else { die(... unknown flag ...) }` branch:

```cpp
} else if (flag == "--extra-projects-root") {
    args.extra_projects_roots.emplace_back(next());
} else if (flag == "--no-config") {
    args.read_config = false;
```

(Keep the existing `--version`, `--projects-root`, etc. branches as they are.)

- [ ] **Step 3: Build to verify it compiles**

Run: `cmake --build C:\Users\mtsch\claude-walker\cpp\build --config Release`
Expected: build succeeds.

- [ ] **Step 4: Smoke-test that the new flags parse without error**

Run: `C:\Users\mtsch\claude-walker\cpp\build\Release\walker.exe --period 86400 --win-start 0 --extra-projects-root C:\nonexistent --no-config`
Expected: exit 0, one JSON line on stdout, `trailing_usd` and `window_usd` numeric (probably matching the current single-root totals since the extra is nonexistent — no behavior change yet).

### Task 2.2: Modify `discover_groups` to accept a vector of roots

**Files:**
- Modify: `C:\Users\mtsch\claude-walker\cpp\main.cpp` (discover_groups at lines 282-345)

- [ ] **Step 1: Change signature and loop body**

Replace the function (around lines 282-345). Show the full new body:

```cpp
static GroupMap discover_groups(
    const std::vector<fs::path>& roots,
    double earliest)
{
    GroupMap groups;

    for (const fs::path& root : roots) {
        std::error_code ec;
        if (!fs::exists(root, ec)) continue;

        // Parents: <root>/<slug>/<session_id>.jsonl
        for (auto& slug_entry : fs::directory_iterator(root, ec)) {
            if (!slug_entry.is_directory()) continue;
            std::string slug = slug_entry.path().filename().string();

            for (auto& file_entry : fs::directory_iterator(slug_entry.path(), ec)) {
                const auto& path = file_entry.path();

                if (!file_entry.is_regular_file()) continue;
                if (path.extension() != ".jsonl") continue;

                auto mtime = fs::last_write_time(path, ec);
                if (!ec) {
                    auto sys_time = std::chrono::time_point_cast<std::chrono::seconds>(
                        std::chrono::clock_cast<std::chrono::system_clock>(mtime));
                    double mtime_unix = static_cast<double>(sys_time.time_since_epoch().count());
                    if (mtime_unix < earliest) continue;
                }

                std::string sid = path.stem().string();
                groups[group_key(slug, sid)].push_back(path);
            }

            // Subagents: <root>/<slug>/<session_id>/subagents/agent-*.jsonl
            for (auto& session_entry : fs::directory_iterator(slug_entry.path(), ec)) {
                if (!session_entry.is_directory()) continue;
                std::string sid = session_entry.path().filename().string();

                fs::path subagents_dir = session_entry.path() / "subagents";
                if (!fs::is_directory(subagents_dir, ec)) continue;

                for (auto& agent_entry : fs::directory_iterator(subagents_dir, ec)) {
                    const auto& apath = agent_entry.path();
                    if (!agent_entry.is_regular_file()) continue;
                    if (apath.extension() != ".jsonl") continue;

                    std::string fname = apath.filename().string();
                    if (fname.substr(0, 6) != "agent-") continue;

                    auto mtime = fs::last_write_time(apath, ec);
                    if (!ec) {
                        auto sys_time = std::chrono::time_point_cast<std::chrono::seconds>(
                            std::chrono::clock_cast<std::chrono::system_clock>(mtime));
                        double mtime_unix = static_cast<double>(sys_time.time_since_epoch().count());
                        if (mtime_unix < earliest) continue;
                    }

                    groups[group_key(slug, sid)].push_back(apath);
                }
            }
        }
    }

    return groups;
}
```

Note the only changes from the original: signature accepts `std::vector<fs::path>&`, and an outer `for (root : roots)` loop wraps the rest.

- [ ] **Step 2: Update the include in main.cpp**

Add near the other includes at the top of `main.cpp`:

```cpp
#include "walker_roots.hpp"
```

- [ ] **Step 3: Update run_cost (lines 437-507)**

Replace the `fs::path root = args.projects_root.value_or(default_projects_root());` line + the `GroupMap groups = discover_groups(root, earliest);` line with:

```cpp
fs::path primary = args.projects_root.value_or(default_projects_root());
std::vector<fs::path> roots = walker::resolve_roots(
    primary, args.extra_projects_roots, args.read_config);

GroupMap groups = discover_groups(roots, earliest);
```

(Everything else in run_cost is unchanged.)

- [ ] **Step 4: Build**

Run: `cmake --build C:\Users\mtsch\claude-walker\cpp\build --config Release`
Expected: build succeeds.

### Task 2.3: Run existing conformance with `--no-config` (sanity check no regressions)

Existing conformance walks corpus via `--projects-root <CORPUS>`. With our changes, that's "primary = CORPUS, no extras, no config." Behavior should be identical to before.

But the conformance harness doesn't pass `--no-config` yet. If the dev machine has a `walker-roots.json`, conformance would now pick it up — which it shouldn't for fixture isolation. **Fix conformance first, then re-run.**

- [ ] **Step 1: Patch conformance.py to always pass `--no-config`**

Modify: `C:\Users\mtsch\claude-walker\shared\conformance.py` — the `run_walker` function (around line 69):

```python
def run_walker(binary: Path, meta: dict, projects_root: Path, extras: list[Path] | None = None) -> dict:
    """Run the walker binary against `projects_root`, return parsed JSON output."""
    cmd = [
        str(binary),
        "--period", str(meta["period_seconds"]),
        "--win-start", repr(meta["win_start_unix"]),
        "--now", repr(meta["now_unix"]),
        "--projects-root", str(projects_root),
        "--no-config",
    ]
    for extra in extras or []:
        cmd.extend(["--extra-projects-root", str(extra)])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(
            f"{binary.name} exited {result.returncode}\n"
            f"stderr:\n{result.stderr}"
        )
    line = result.stdout.strip().splitlines()[-1]
    return json.loads(line)
```

- [ ] **Step 2: Run conformance against cpp**

Run: `cd C:\Users\mtsch\claude-walker && python shared\conformance.py cpp`
Expected: every fixture passes, aggregate passes. (`--no-config` should be a no-op when no walker-roots.json exists on the dev box, OR cause the file to be skipped if one does.)

- [ ] **Step 3: Commit**

```bash
cd C:\Users\mtsch\claude-walker
git add cpp/main.cpp shared/conformance.py
git commit -m "walker: cost-mode multi-root + --no-config / --extra-projects-root"
```

---

## Phase 3: beacons-history multi-root

### Task 3.1: Add flags to history args parser

**Files:**
- Modify: `C:\Users\mtsch\claude-walker\cpp\beacons.cpp` (HistoryArgs + parse_history_args, lines 365-410)

- [ ] **Step 1: Extend HistoryArgs**

Replace lines 365-371:

```cpp
struct HistoryArgs {
    uint64_t period_seconds = 0;
    bool have_period = false;
    double win_start_unix = 0.0;
    std::optional<fs::path> projects_root;
    std::vector<fs::path> extra_projects_roots;
    bool read_config = true;
    std::optional<double> now_unix;
};
```

- [ ] **Step 2: Add flag handling in parse_history_args**

Before the final `} else { err = "unknown flag..."; }` (around line 403), insert:

```cpp
} else if (flag == "--extra-projects-root") {
    auto v = need_value(flag);
    if (!v) return std::nullopt;
    out.extra_projects_roots.emplace_back(*v);
} else if (flag == "--no-config") {
    out.read_config = false;
```

- [ ] **Step 3: Build**

Run: `cmake --build C:\Users\mtsch\claude-walker\cpp\build --config Release`
Expected: success.

### Task 3.2: Modify `discover_history_groups` to accept multiple roots

**Files:**
- Modify: `C:\Users\mtsch\claude-walker\cpp\beacons.cpp` (lines 509-545)

- [ ] **Step 1: Add the walker_roots.hpp include**

Add at the top with other includes:

```cpp
#include "walker_roots.hpp"
```

- [ ] **Step 2: Replace `discover_history_groups`**

```cpp
HistoryGroups discover_history_groups(const std::vector<fs::path>& roots) {
    HistoryGroups groups;
    std::error_code ec;

    for (const fs::path& root : roots) {
        if (!fs::exists(root, ec)) continue;

        for (auto& slug_entry : fs::directory_iterator(root, ec)) {
            if (!slug_entry.is_directory()) continue;
            std::string slug = slug_entry.path().filename().string();

            // Parents: <root>/<slug>/<sid>.jsonl
            for (auto& file_entry : fs::directory_iterator(slug_entry.path(), ec)) {
                if (!file_entry.is_regular_file()) continue;
                const auto& path = file_entry.path();
                if (path.extension() != ".jsonl") continue;
                std::string sid = path.stem().string();
                groups[{slug, sid}].push_back(path);
            }

            // Subagents: <root>/<slug>/<sid>/subagents/agent-*.jsonl
            for (auto& session_entry : fs::directory_iterator(slug_entry.path(), ec)) {
                if (!session_entry.is_directory()) continue;
                std::string sid = session_entry.path().filename().string();

                fs::path subdir = session_entry.path() / "subagents";
                if (!fs::is_directory(subdir, ec)) continue;
                for (auto& agent_entry : fs::directory_iterator(subdir, ec)) {
                    if (!agent_entry.is_regular_file()) continue;
                    const auto& apath = agent_entry.path();
                    if (apath.extension() != ".jsonl") continue;
                    std::string fname = apath.filename().string();
                    if (fname.substr(0, 6) != "agent-") continue;
                    groups[{slug, sid}].push_back(apath);
                }
            }
        }
    }
    return groups;
}
```

- [ ] **Step 3: Update run_history to use resolve_roots**

In `run_history` (around line 575), replace `fs::path root = parsed.projects_root.value_or(walker::default_projects_root());` and the subsequent `HistoryGroups groups = discover_history_groups(root);` with:

```cpp
fs::path primary = parsed.projects_root.value_or(walker::default_projects_root());
std::vector<fs::path> roots = walker::resolve_roots(
    primary, parsed.extra_projects_roots, parsed.read_config);
HistoryGroups groups = discover_history_groups(roots);
```

- [ ] **Step 4: Build + smoke-test**

Run: `cmake --build C:\Users\mtsch\claude-walker\cpp\build --config Release`
Then: `C:\Users\mtsch\claude-walker\cpp\build\Release\walker.exe beacons-history --period 604800 --win-start 0 --no-config`
Expected: exit 0, JSON line with `pairs`, `session_count`, `n_pairs`, `bias_factor` fields.

- [ ] **Step 5: Commit**

```bash
cd C:\Users\mtsch\claude-walker
git add cpp/beacons.cpp
git commit -m "walker: beacons-history multi-root"
```

---

## Phase 4: beacons-latest multi-root

### Task 4.1: Add flags + roots loop to run_latest

**Files:**
- Modify: `C:\Users\mtsch\claude-walker\cpp\beacons.cpp` (LatestArgs + parse_latest_args + run_latest)

- [ ] **Step 1: Extend LatestArgs (replace lines 324-328)**

```cpp
struct LatestArgs {
    std::string session_id;
    std::optional<fs::path> projects_root;
    std::vector<fs::path> extra_projects_roots;
    bool read_config = true;
    std::optional<double> now_unix;
};
```

- [ ] **Step 2: Add flag handling in parse_latest_args**

Before the final `} else { err = "unknown flag..."; }` (around line 356), insert:

```cpp
} else if (flag == "--extra-projects-root") {
    auto v = need_value(flag);
    if (!v) return std::nullopt;
    out.extra_projects_roots.emplace_back(*v);
} else if (flag == "--no-config") {
    out.read_config = false;
```

- [ ] **Step 3: Update run_latest to loop over roots**

In `run_latest`, replace the discovery section (around lines 440-466) so it iterates the resolved roots list. The new body:

```cpp
fs::path primary = parsed.projects_root.value_or(walker::default_projects_root());
std::vector<fs::path> roots = walker::resolve_roots(
    primary, parsed.extra_projects_roots, parsed.read_config);
double now_unix = parsed.now_unix.value_or(walker::current_unix());

std::vector<fs::path> paths;
std::string parent_filename = parsed.session_id + ".jsonl";
std::string subagent_filename = "agent-" + parsed.session_id + ".jsonl";

for (const fs::path& root : roots) {
    std::error_code ec;
    if (!fs::exists(root, ec)) continue;

    for (auto& slug_entry : fs::directory_iterator(root, ec)) {
        if (!slug_entry.is_directory()) continue;
        fs::path candidate = slug_entry.path() / parent_filename;
        if (fs::is_regular_file(candidate, ec)) paths.push_back(candidate);

        for (auto& session_entry : fs::directory_iterator(slug_entry.path(), ec)) {
            if (!session_entry.is_directory()) continue;
            fs::path subdir = session_entry.path() / "subagents";
            if (!fs::is_directory(subdir, ec)) continue;
            fs::path scan = subdir / subagent_filename;
            if (fs::is_regular_file(scan, ec)) paths.push_back(scan);
        }
    }
}
```

(The rest of run_latest — finding the latest beacon across `paths` — stays unchanged.)

- [ ] **Step 4: Build**

Run: `cmake --build C:\Users\mtsch\claude-walker\cpp\build --config Release`
Expected: success.

- [ ] **Step 5: Commit**

```bash
cd C:\Users\mtsch\claude-walker
git add cpp/beacons.cpp
git commit -m "walker: beacons-latest multi-root"
```

---

## Phase 5: SPEC.md update + version bump

### Task 5.1: Update SPEC.md and version string

**Files:**
- Modify: `C:\Users\mtsch\claude-walker\SPEC.md`
- Modify: `C:\Users\mtsch\claude-walker\cpp\main.cpp` (version string)

- [ ] **Step 1: Add a "Roots" section to SPEC.md**

Insert between "## CLI contract" and "## Discovery" in `SPEC.md`:

````markdown
## Roots

Every subcommand walks an effective set of project roots assembled as:

1. **Primary root.** From `--projects-root <path>` if given, else
   `~/.claude/projects`.
2. **CLI extras.** Zero or more `--extra-projects-root <path>` flags.
3. **Config extras.** Read from `~/.claude/walker-roots.json` unless
   `--no-config` is passed.

### Config file shape

`~/.claude/walker-roots.json`:

```json
{
  "extra_roots": [
    "/mnt/chonkers/Users/mtsch/.claude/projects"
  ]
}
```

Single key `extra_roots`: array of absolute paths. Per-host; NOT
synced via memory-sync. Missing file → no extras. Malformed JSON →
stderr diagnostic, treat as no extras (must NOT error).

### Resolution

The combined list is:

- Deduplicated by `fs::canonical` (realpath); if `canonical` fails for
  an entry, fall back to its lexically-normalized form.
- Filtered to existing directories. Non-existent extras are skipped
  silently with a stderr diagnostic. (This is the SMB-mount-unreachable
  case — walker must keep going.)
- Order: primary first, CLI extras in order, config extras in order.
  Order is informational; results are aggregated and must not depend on
  it within float epsilon.

Per-group dedup (`seen_ids` on `message.id`) is unchanged. Per-file
mtime filter is unchanged. All applied uniformly across roots.
````

- [ ] **Step 2: Bump version**

In `C:\Users\mtsch\claude-walker\cpp\main.cpp`, change line 70:

```cpp
        std::cout << "cpp/0.4.0\n";
```

In `SPEC.md`, find the existing "## Versioning" section and update the example. If there's a `spec_version` mention, leave the spec version implicit; the README and binaries carry the visible bump.

- [ ] **Step 3: Verify**

Run: `C:\Users\mtsch\claude-walker\cpp\build\Release\walker.exe --version`
Expected: `cpp/0.4.0`

- [ ] **Step 4: Commit**

```bash
cd C:\Users\mtsch\claude-walker
git add SPEC.md cpp/main.cpp
git commit -m "spec: document multi-root resolution, bump cpp/0.4.0"
```

---

## Phase 6: Conformance fixtures + harness

Adds two new conformance scenarios in a separate test path that exercises multi-root discovery without polluting the existing aggregate sums.

### Task 6.1: Create the multi-root fixture directories

**Files:**
- Create: `C:\Users\mtsch\claude-walker\shared\corpus\multi_root\10-merge-roots\primary\<slug>\<sid>.jsonl`
- Create: `C:\Users\mtsch\claude-walker\shared\corpus\multi_root\10-merge-roots\extra\<slug>\<sid>.jsonl`
- Create: `C:\Users\mtsch\claude-walker\shared\corpus\multi_root\10-merge-roots\expected.json`
- Create: `C:\Users\mtsch\claude-walker\shared\corpus\multi_root\11-unreachable\primary\<slug>\<sid>.jsonl`
- Create: `C:\Users\mtsch\claude-walker\shared\corpus\multi_root\11-unreachable\expected.json`

- [ ] **Step 1: Create `10-merge-roots/primary/multi-a/sess-a.jsonl`**

```json
{"timestamp":"2026-05-09T12:00:00Z","message":{"role":"assistant","id":"msg-a-1","model":"claude-opus-4-7","usage":{"input_tokens":1000,"output_tokens":500,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}
```

That single line yields cost = (1000 * 5.0 + 500 * 25.0) / 1_000_000 = $0.0175.

- [ ] **Step 2: Create `10-merge-roots/extra/multi-b/sess-b.jsonl`**

```json
{"timestamp":"2026-05-09T12:00:00Z","message":{"role":"assistant","id":"msg-b-1","model":"claude-sonnet-4-6","usage":{"input_tokens":2000,"output_tokens":1000,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}
```

Cost = (2000 * 3.0 + 1000 * 15.0) / 1_000_000 = $0.021.

- [ ] **Step 3: Create `10-merge-roots/expected.json`**

```json
{
  "_meta": {
    "now_unix": 1778414400.0,
    "period_seconds": 604800,
    "win_start_unix": 1778000000.0,
    "note": "Multi-root conformance fixture."
  },
  "expected": {
    "trailing_usd": 0.0385,
    "window_usd": 0.0385
  },
  "primary_root": "primary",
  "extra_roots": ["extra"]
}
```

Cost total: $0.0175 + $0.021 = $0.0385.

- [ ] **Step 4: Create `11-unreachable/primary/multi-a/sess-c.jsonl`**

```json
{"timestamp":"2026-05-09T12:00:00Z","message":{"role":"assistant","id":"msg-c-1","model":"claude-opus-4-7","usage":{"input_tokens":1000,"output_tokens":500,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}
```

Cost = $0.0175 (same shape as 10-merge-roots primary).

- [ ] **Step 5: Create `11-unreachable/expected.json`**

```json
{
  "_meta": {
    "now_unix": 1778414400.0,
    "period_seconds": 604800,
    "win_start_unix": 1778000000.0,
    "note": "Multi-root unreachable extra: walker must skip silently and return primary-only totals."
  },
  "expected": {
    "trailing_usd": 0.0175,
    "window_usd": 0.0175
  },
  "primary_root": "primary",
  "extra_roots": ["__does_not_exist__"]
}
```

### Task 6.2: Add multi-root check path to conformance harness

**Files:**
- Modify: `C:\Users\mtsch\claude-walker\shared\conformance.py`

- [ ] **Step 1: Add a `check_multi_root` helper**

Insert near the other `check_*` functions:

```python
MULTI_ROOT_CORPUS = ROOT / "shared" / "corpus" / "multi_root"


def check_multi_root(lang: str, binary: Path) -> bool:
    """Run each multi-root scenario; assert binary sums match expected.json."""
    if not MULTI_ROOT_CORPUS.is_dir():
        return True  # no scenarios — skip cleanly
    all_ok = True
    for scenario_dir in sorted(MULTI_ROOT_CORPUS.iterdir()):
        if not scenario_dir.is_dir():
            continue
        expected_file = scenario_dir / "expected.json"
        if not expected_file.is_file():
            continue
        data = json.loads(expected_file.read_text())
        meta = data["_meta"]
        primary = scenario_dir / data["primary_root"]
        extras = [scenario_dir / r for r in data["extra_roots"]]
        try:
            got = run_walker(binary, meta, primary, extras=extras)
        except Exception as e:
            print(f"  [{lang:>4s}] {scenario_dir.name:<22s} FAIL  {e}")
            all_ok = False
            continue
        target = data["expected"]
        ok, dt, dw = within_tolerance(got, target)
        badge = " OK " if ok else "FAIL"
        print(
            f"  [{lang:>4s}] {scenario_dir.name:<22s} {badge}  "
            f"trailing=${got.get('trailing_usd', 0):.6f} (d=${dt:+.6f})  "
            f"window=${got.get('window_usd', 0):.6f} (d=${dw:+.6f})"
        )
        if not ok:
            all_ok = False
    return all_ok
```

- [ ] **Step 2: Wire into the main loop**

Find the main function (or the per-lang dispatch loop) where `check_aggregate` and per-fixture checks are called, and add a call to `check_multi_root(lang, binary)` after the per-fixture loop. Have it contribute to the overall pass/fail tally the same way other checks do.

- [ ] **Step 3: Run conformance**

Run: `cd C:\Users\mtsch\claude-walker && python shared\conformance.py cpp`
Expected: every existing fixture passes AND both multi-root scenarios pass.

- [ ] **Step 4: Commit**

```bash
cd C:\Users\mtsch\claude-walker
git add shared/corpus/multi_root/ shared/conformance.py
git commit -m "test: multi-root conformance fixtures + harness"
```

---

## Phase 7: Python fallback in statusline_lib.py

### Task 7.1: Add `_walker_root_list()` helper

**Files:**
- Modify: `C:\Users\mtsch\schoen-claude-status\statusline_lib.py`

- [ ] **Step 1: Add the helper near the other private file helpers**

Insert after `_find_walker_binary()` (around line 568):

```python
_WALKER_ROOTS_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "walker-roots.json"
)


def _walker_root_list():
    """Default root + extras from walker-roots.json. Mirrors C++ resolve_roots.

    Failure modes match the SPEC: missing file => no extras; malformed JSON =>
    stderr message + no extras. Only directories that exist on disk make it
    into the result. Realpath-deduped.
    """
    home = os.path.expanduser("~")
    default = os.path.join(home, ".claude", "projects")
    all_paths = [default]
    try:
        with open(_WALKER_ROOTS_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        extras = cfg.get("extra_roots") or []
        if isinstance(extras, list):
            all_paths.extend(str(p) for p in extras if isinstance(p, str) and p)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        # Malformed JSON: log, fall through. Don't crash the statusline.
        print(
            f"statusline_lib: ignoring malformed {_WALKER_ROOTS_CONFIG_PATH}: {exc}",
            file=__import__("sys").stderr,
        )

    seen = set()
    result = []
    for p in all_paths:
        try:
            canon = os.path.realpath(p)
        except OSError:
            canon = os.path.normpath(p)
        if not os.path.isdir(canon):
            continue
        if canon in seen:
            continue
        seen.add(canon)
        result.append(canon)
    return result
```

### Task 7.2: Use the helper in `_walk_pace_buckets`

**Files:**
- Modify: `C:\Users\mtsch\schoen-claude-status\statusline_lib.py` (`_walk_pace_buckets` at lines 667-760)

- [ ] **Step 1: Replace the `proj_root` lookup + discovery glob loop**

Inside `_walk_pace_buckets`, replace lines 694-724 (from `home = os.path.expanduser("~")` through the second `for path in glob.glob(sub_pattern):` loop) with a roots-loop wrapper:

```python
    roots = _walker_root_list()
    if not roots:
        return 0.0, 0.0
    now = datetime.now(timezone.utc).timestamp()
    period_cutoff = now - period_seconds
    earliest = min(period_cutoff, win_start_unix)

    # Group by (slug, session_id) so each work unit owns its own dedup set.
    groups = {}
    for proj_root in roots:
        for path in glob.glob(os.path.join(proj_root, "*", "*.jsonl")):
            try:
                if os.path.getmtime(path) < earliest:
                    continue
            except OSError:
                continue
            slug = os.path.basename(os.path.dirname(path))
            session_id = os.path.splitext(os.path.basename(path))[0]
            groups.setdefault((slug, session_id), []).append(path)
        sub_pattern = os.path.join(proj_root, "*", "*", "subagents", "agent-*.jsonl")
        for path in glob.glob(sub_pattern):
            try:
                if os.path.getmtime(path) < earliest:
                    continue
            except OSError:
                continue
            sub_dir = os.path.dirname(path)
            session_dir = os.path.dirname(sub_dir)
            session_id = os.path.basename(session_dir)
            slug = os.path.basename(os.path.dirname(session_dir))
            groups.setdefault((slug, session_id), []).append(path)
```

(The rest of the function — parallel pool dispatch — is unchanged.)

### Task 7.3: Bump pace-cache filename to invalidate stale entries

**Files:**
- Modify: `C:\Users\mtsch\schoen-claude-status\statusline_lib.py`

- [ ] **Step 1: Rename the constant**

Replace line 488-489 (the `_PACE_CACHE_PATH = ...` assignment):

```python
_PACE_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", ".statusline-pace-cache-v2.json"
)
```

Why v2: the v1 cache key didn't include roots, so a stale v1 entry on disk would serve incorrect numbers (single-root sums) until the TTL expired. The rename forces a one-time miss.

### Task 7.4: Add a small Python self-test script

**Files:**
- Create: `C:\Users\mtsch\schoen-claude-status\.claude\scripts\verify_multi_root_fallback.py`

- [ ] **Step 1: Write the test**

The test sets HOME (USERPROFILE on Windows) to a tmp dir so `~` expansion lands inside that tmp world. Builds a default-root + extra-root layout there, drops a `walker-roots.json` next to them, runs the walker pace function, asserts the dollar total.

```python
"""Stand-alone verifier for statusline_lib._walker_root_list and
_walk_pace_buckets multi-root behavior.

Run:
    python .claude/scripts/verify_multi_root_fallback.py

Builds a tmp filesystem layout, points HOME at it, asserts dollar totals.
Cleans up on exit even on failure.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)


def make_jsonl(path, model, input_tokens, output_tokens):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    entry = {
        "timestamp": iso,
        "message": {
            "role": "assistant",
            "id": f"msg-{os.path.basename(path)}",
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    tmp = tempfile.mkdtemp(prefix="walker-fallback-test-")
    old_home = os.environ.get("HOME")
    old_userprofile = os.environ.get("USERPROFILE")
    try:
        # Point ~ at tmp so default-root resolution lands in our sandbox.
        os.environ["HOME"] = tmp
        os.environ["USERPROFILE"] = tmp

        default_root = os.path.join(tmp, ".claude", "projects")
        extra_root = os.path.join(tmp, "extra-projects")

        make_jsonl(
            os.path.join(default_root, "slug-default", "sess-d.jsonl"),
            "claude-opus-4-7", 1000, 500,
        )  # $0.0175
        make_jsonl(
            os.path.join(extra_root, "slug-extra", "sess-e.jsonl"),
            "claude-sonnet-4-6", 2000, 1000,
        )  # $0.021

        config_path = os.path.join(tmp, ".claude", "walker-roots.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"extra_roots": [extra_root]}, f)

        # Import (or reload) statusline_lib AFTER HOME is reset so any
        # module-level path resolution uses the sandboxed home.
        if "statusline_lib" in sys.modules:
            importlib.reload(sys.modules["statusline_lib"])
        import statusline_lib

        roots = statusline_lib._walker_root_list()
        assert os.path.realpath(default_root) in roots, \
            f"default root missing: {roots}"
        assert os.path.realpath(extra_root) in roots, \
            f"extra root missing: {roots}"

        now = datetime.now(timezone.utc).timestamp()
        trailing, window = statusline_lib._walk_pace_buckets(
            period_seconds=604800,
            win_start_unix=now - 86400,
        )

        expected = 0.0175 + 0.021
        assert abs(trailing - expected) < 0.001, \
            f"trailing got ${trailing:.4f}, expected ${expected:.4f}"
        print(f"OK: trailing=${trailing:.4f} window=${window:.4f}")
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if old_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = old_userprofile
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test**

Run: `python C:\Users\mtsch\schoen-claude-status\.claude\scripts\verify_multi_root_fallback.py`
Expected: `OK: trailing=$0.0385 window=$0.0210` (or similar — the precise window value depends on `win_start_unix` vs entry timestamp).

- [ ] **Step 3: Commit**

```bash
cd C:\Users\mtsch\schoen-claude-status
git add statusline_lib.py .claude/scripts/verify_multi_root_fallback.py
git commit -m "feat: multi-root walker support in Python fallback"
```

---

## Phase 8: End-to-end verification

Confirm the production wiring works on the dev box.

### Task 8.1: Live verify on dev box

- [ ] **Step 1: Write a real walker-roots.json**

Create `C:\Users\mtsch\.claude\walker-roots.json`:

```json
{
  "extra_roots": [
    "Y:\\.claude\\projects"
  ]
}
```

(`Y:\` is the SMB mount of llamabox on chonkers per the user's CLAUDE.md.)

- [ ] **Step 2: Run the walker manually**

Run: `C:\Users\mtsch\claude-walker\cpp\build\Release\walker.exe --period 604800 --win-start 0`
Expected: exit 0, JSON line. `files_walked` count is higher than the no-extras baseline (capture the baseline by running once with `--no-config` for comparison).

- [ ] **Step 3: Confirm bias-factor recompute**

Run: `C:\Users\mtsch\claude-walker\cpp\build\Release\walker.exe beacons-history --period 604800 --win-start 0`
Expected: `session_count` is higher than `--no-config` run. `bias_factor` may change.

- [ ] **Step 4: Verify statusline picks it up**

Open a fresh Claude Code session, observe the `wk:` line. Compared to a session before this change, the projected `+Xh` should reflect both machines' burn (typically a tighter / more conservative projection if both machines were active).

- [ ] **Step 5: Remove the local config (cleanup if testing only) OR leave it in place**

Decision is the user's at runtime — the walker-roots.json is the deployment artifact, not a code change.

---

## Self-review notes

Spec coverage check:
- **Problem / Architecture sections** of the spec → covered by Phases 1-7 (walker changes) and Phase 7 (Python fallback).
- **Config schema** → Phase 1 implementation + Phase 5 SPEC.md docs.
- **Failure modes** (missing/malformed/unreachable) → Phase 1 helper + Phase 6 fixture 11 (unreachable).
- **CLI flags** (`--extra-projects-root`, `--no-config`) → Phases 2/3/4 (one per subcommand).
- **Effective root set** order → Phase 1 helper + Phase 5 docs.
- **Discovery & walking** → Phases 2/3/4 sub-tasks.
- **SPEC.md update** → Phase 5.
- **Python fallback** → Phase 7.
- **Conformance & tests** → Phase 6.
- **Risks: mount latency on the hot path** → flagged in spec as deferred. Not addressed in this plan; would be a follow-up if symptoms appear.

Type / naming consistency:
- `walker::resolve_roots(primary, cli_extras, read_config)` — same signature used from `main.cpp:run_cost`, `beacons.cpp:run_history`, `beacons.cpp:run_latest`.
- `--no-config` and `--extra-projects-root` spelled consistently across three argument parsers (cost, history, latest).
- Python `_walker_root_list()` mirrors C++ behavior; cache filename bumped to `_v2` to invalidate stale entries.

Out-of-scope reminders (also flagged in spec):
- `cost-estimator` skill is NOT updated here. Retrospective spend analysis remains single-machine until a follow-up.
