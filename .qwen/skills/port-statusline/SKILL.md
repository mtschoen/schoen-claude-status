---
name: port-statusline
description: Port custom statusline scripts between AI CLI tools (Claude Code <-> Qwen Code) by adapting payload parsing and reusing shared rendering logic
source: auto-skill
extracted_at: '2026-05-31T21:22:07.299Z'
---

# Porting Statuslines Between AI CLI Tools

When porting custom statusline scripts from one AI CLI tool to another (e.g., Claude Code to Qwen Code), follow this systematic approach to maximize code reuse while adapting to different payload schemas.

## Procedure

### 1. Analyze Both Payload Schemas

Read the existing statusline entry point to understand what data it consumes:

```bash
# For Claude Code
read statusline.py
read statusline_lib/__init__.py

# For Qwen Code, check documentation
# Qwen provides: session_id, version, model.display_name, context_window.*,
#                 workspace.current_dir, git.branch, metrics.models.*, metrics.files, vim.mode
```

Key differences to watch for:
- **Claude Code**: `model.id`, `context_window.current_usage` (dict with breakdown), `cost.total_cost_usd`, `rate_limits`, `transcript_path`
- **Qwen Code**: `model.display_name`, `context_window.current_usage` (flat number), no cost/quota data, `metrics.models.<id>.tokens/api`

### 2. Identify Reusable Components

Shared library modules can typically be reused with minor modifications:

- **cost.py**: Modify `format_model_badge()` to add target tool's model families
- **sessions.py**: Update `_process_matches()` to detect target tool's process names
- **base.py**: Color constants and formatting helpers work unchanged
- **cache formatting**: Adapt to available token breakdown (Qwen has `cached` field, not read/write split)

### 3. Add Target-Specific Formatters

Create new formatters in `cost.py` for data unique to the target tool:

```python
def format_qwen_tokens(tokens):
    """Format {prompt, completion, cached, thoughts} with colored icons"""

def format_qwen_api_stats(api):
    """Format {total_requests, total_errors, total_latency_ms}"""

def format_qwen_files(files):
    """Format {total_lines_added, total_lines_removed}"""
```

### 4. Create New Entry Point

Create `<target>_statusline.py` that:
- Reads target tool's JSON payload from stdin
- Extracts fields using target's schema
- Calls shared formatters from `statusline_lib`
- Outputs 2-line layout: `[host] cwd (branch)` then `model | ctx | cache | metrics`

Key adaptations:
- Use `model.display_name` instead of `model.id`
- Handle flat `current_usage` number instead of dict breakdown
- Compute cache stats from available fields (e.g., Qwen's `cached` = reads)
- Skip unavailable features (cost, quota, beacons, transcript walking)

### 5. Create Shell Shims

Create both `.sh` and `.bat` shims that:
- Use fastest Python launcher (`py -3` on Windows, `python3` on Unix)
- Forward stdin to the new entry point
- Match naming convention: `<target>-statusline-command.{sh,bat}`

### 6. Test with Synthetic Payload

Generate test JSON matching target schema and pipe to new entry point:

```bash
py -3 -c "import json,sys; json.dump({...target_schema...},sys.stdout)" | py -3 <target>_statusline.py
```

Verify all segments render correctly with proper colors and formatting.

## Common Pitfalls

- **Model badge parsing**: Different tools use different naming conventions (e.g., "qwen-3-235b" vs "claude-opus-4-8")
- **Context window**: Some tools provide flat usage numbers, others provide detailed breakdowns
- **Process detection**: Update process name matching to detect target tool's executable
- **Missing features**: Accept that some features (cost tracking, quota monitoring) may not be available if the target tool doesn't expose the data

## File Structure After Port

```
project/
├── statusline.py              # Original Claude Code entry
├── qwen_statusline.py         # New Qwen Code entry
├── statusline-command.sh      # Claude Code Unix shim
├── qwen-statusline-command.sh # Qwen Code Unix shim
├── qwen-statusline-command.bat # Qwen Code Windows shim
└── statusline_lib/            # Shared library (modified to support both)
    ├── __init__.py
    ├── cost.py                # Added Qwen formatters + model families
    ├── sessions.py            # Added qwen process detection
    └── ...
```

## Configuration

Wire up in target tool's settings:

**Qwen Code** (`~/.qwen/settings.json`):
```json
{
  "ui": {
    "statusLine": {
      "type": "command",
      "command": "py -3 /path/to/qwen_statusline.py"
    }
  }
}
```

Hot-reloads without restart.

## Lessons from Claude Code → Qwen Code Port

### Cache display: match Claude Code format exactly

**Rule:** The cache column should use the same `read / write / hit%` format as Claude Code status bar. Never use custom emoji-based displays (🗄💭). Never show `↗` and `↘` arrows for cache — those belong to token counts.

**Why:** Users find custom emojis confusing and inconsistent. The cache column is one of the most-read parts of the status line; consistency with Claude Code matters.

**How to apply:** In target-specific formatters, always use the existing `format_cache()` or write an equivalent that produces `read / write / hit%`. Use `color_high_good()` for the percentage.

### `color_high_good()` already appends `%`

`color_high_good(73, 90, 75)` returns `\x1b[38;5;220m73%\x1b[0m` — it includes the `%` character. Don't double-append: `f"{color_high_good(hit_pct, 90, 75)}{hit_pct:.0f}%"` produces `73%73%`.

### Qwen's `prompt` includes `cached`

Qwen's `metrics.models.<id>.tokens.prompt` = `cache_read_input_tokens + non_cached_input`. The `cached` field is a subset of `prompt`, not separate. To show independent values:
- Cache column: `read = cached`, `write = prompt - cached`, `hit% = cached / prompt`
- Token column: `↑prompt ↓completion` (full totals, not split)
- Never show both raw `prompt` and `cached` as if they were additive

### Use ↑/↓ arrows for token counts (matching Claude status bar)

**Rule:** Use `↑` (input) and `↓` (output) for the token column — not `↗/↘` diagonals. This matches the Claude Code status bar exactly.

**How to apply:** In `format_qwen_tokens()`, use `↑` for prompt and `↓` for completion tokens.

### Thinking tokens: append as `(thk NNNK)`

Thinking/thought tokens are available in Qwen's payload (`metrics.models.<id>.tokens.thoughts`) but don't have a Claude Code equivalent. Append them to the status line as `(thk10.1K)` using the mauve `CTX_DENOM` color. Keep it subtle — don't add emojis.

### Cache hit rate: use a single source, always live metrics

**Problem:** If you mix transcript-walked cache totals (old turns) with live metrics (current snapshot), the numbers diverge. The user will see `1.95M / 0 / 64% hit` from Claude Code's transcript walk but different numbers from Qwen's live snapshot.

**Fix:** Use only Qwen's live `metrics.models.<id>.tokens` for all cache/token calculations. One data source, one rendering pass. No transcript walk.

### Installer `--platform` flag

The installer now accepts `--platform claude|qwen|both`. Default is `claude`. Use `--platform qwen` for Qwen-only, `--platform both` for dual install.

### Shell test gotcha on Windows

`echo {"json": ...} | cmd.exe` mangles JSON (cmd interprets `{}` as glob). Use file-based redirects for reliable testing:
```bash
echo {"json":"here"} > test.json
py -3 script.py < test.json
```

### Don't use `include_hit` parameter on `format_qwen_tokens()`

The cache column displays hit rate separately. The token column should show raw `↑prompt ↓completion` without any hit rate appended. Remove `include_hit` from the function signature.

### Add new formatters to both `cost.py` and `__init__.py`

Every new formatter needs to be:
1. Defined in `statusline_lib/cost.py`
2. Imported in `statusline_lib/__init__.py`'s import block
3. Added to `__all__` in `__init__.py`
4. Imported in the entry point (e.g., `qwen_statusline.py`)

Missing any of these causes `NameError: name 'format_qwen_*' is not defined`.

### Process detection: add target tool's executable names

Update `_process_matches()` in `sessions.py` to detect the target tool's process names (e.g., `qwen`, `qwen.exe` for Qwen Code). Also update `_count_via_psutil()` pre-filter to include the new names.

### Always test with file-based stdin on Windows

`echo {"json":...} | cmd.exe` mangles JSON (cmd interprets `{}` as glob). Always use file-based redirects:
```bash
echo {"json":"here"} > test.json
py -3 script.py < test.json
```

### Check for NameError after renaming/removing functions

After refactoring (e.g., renaming `include_hit=True` parameter to no parameter), run the script immediately — old error traces may linger in `.qwen-statusline-error.log` and cause confusion. Delete the error log after each fix cycle.

### Web search: configure DuckDuckGo or use alternative

DuckDuckGo Lite blocks programmatic requests with a CAPTCHA (select ducks puzzle). Workarounds:
- Use `--platform qwen` installer with `--dry-run` instead of web search for config inspection
- If web search is needed, use an MCP web search tool or configure a working search endpoint
- For pricing research, check the model provider's official docs directly rather than scraping
