---
name: cloud-local-payload-handling
description: Qwen Code statusline payloads are identical for cloud and local models — don't add cloud-specific formatters that show redundant information
source: auto-skill
extracted_at: '2026-06-01T05:26:16.680Z'
---

# Cloud vs Local Model Payloads in Qwen Code

**Key finding:** Qwen Code's statusline payload structure is **identical** for cloud models (qwen-max, qwen-plus, etc.) and local models (via Ollama, vLLM, etc.). The `context_window` object contains the same fields regardless of model source.

## Payload Structure (Same for Cloud and Local)

```json
{
  "context_window": {
    "used_percentage": 2.4,
    "remaining_percentage": 97.6,
    "current_usage": 12000,
    "window_size": 500000,
    "total_input_tokens": 24347,
    "total_output_tokens": 430
  }
}
```

All fields are present for both cloud and local models.

## Lesson Learned: Verify Assumptions Before Implementing

**What happened:** I initially assumed cloud and local model payloads differed, and implemented a `format_qwen_context_io()` function to display "cloud-only" fields like `remaining_percentage`, `total_input_tokens`, and `total_output_tokens`.

**The correction:** The user checked the actual payloads and found they're identical. The user said: *"actually, looking back there's actually no difference. So the same decisions from before hold. Make it match the claude status line as closely as possible."*

**The revert:** I removed the `format_qwen_context_io()` function entirely because:
1. `remaining_percentage` is redundant with `current_usage / window_size` (already shown)
2. `total_input_tokens` and `total_output_tokens` duplicate the arrow display (`↑prompt ↓completion`) from `metrics.models.*.tokens`
3. Adding cloud-specific formatters for identical data creates maintenance burden and visual clutter

## Guideline: Match Claude Code Format

Since Qwen Code and Claude Code payloads are structurally similar, **match Claude Code's statusline format as closely as possible**:

- Use the same `model | ctx | cache | tokens | api | thinking | files | vim` layout
- Reuse existing formatters from `statusline_lib` (e.g., `format_context()`, `format_cache()`)
- Only add Qwen-specific formatters for data that Claude Code doesn't provide (e.g., `format_qwen_thinking()` for thought tokens)
- Don't create redundant formatters that show the same information in different formats

## When to Add New Formatters

Only add a new formatter if:

1. **The data is genuinely new** — not already displayed by an existing formatter
2. **The data source is different** — e.g., from a different payload field that provides unique information
3. **The display adds value** — not just showing the same numbers in a different format

**Example of good addition:** `format_qwen_thinking()` — displays thought tokens, which Claude Code doesn't track.

**Example of bad addition:** `format_qwen_context_io()` — displayed `total_input_tokens` and `total_output_tokens`, which are the same values already shown by the arrow display from `metrics.models.*.tokens`.

## Testing Payload Assumptions

Before implementing cloud-vs-local handling:

1. **Check actual payloads** — don't assume based on documentation
2. **Compare field-by-field** — are there actually missing fields, or just different values?
3. **Verify with user** — "I see these fields in the cloud payload. Are they absent in local models?"

If payloads are identical, treat them identically. Don't add conditional logic or graceful degradation for non-existent differences.

## Related Skills

- `port-statusline` — porting between different CLI tools (Claude Code ↔ Qwen Code)
