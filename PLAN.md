# schoen-claude-status — Plan

## Inbox

- [ ] Triage the suspected bugs surfaced by the 2026-06-10 coverage push
      (reported by test-writer agents, deliberately not fixed during it):
      1. qwen.py format_qwen_thinking renders `(thk10.1K)` with no space;
         docstring promises `(thk NNNK)`.
      2. badge.py: a `[1m]`-tagged model on a 200K physical window makes the
         yellow branch unreachable (yellow_tokens 250K > red_tokens 147K);
         every value >=147K goes straight to red.
      3. beacon.py format_beacon: string `eta_seconds` in a transcript raises
         TypeError at `// 60`; sibling _apply_beacon float()-coerces the same
         field defensively.
      4. beacon.py _bias_factor_cached: a fresh cache for a DIFFERENT period
         recomputes and overwrites; two alternating periods would thrash
         (single call site today, latent).
      5. pace.py _parse_pace_line: message id enters seen_ids BEFORE timestamp
         validation, so a truncated line poisons the id and the later complete
         snapshot copy is dropped as a duplicate - silent spend undercount.
      6. pace.py weekly_sustainable_rate has no try/except unlike its
         siblings; a malformed resets_at (string) raises TypeError into the
         render path.
      7. base.py ramp_color_for with warn == danger returns the HOT end for a
         high-good metric, inverting intent (no current caller does this).

- [ ] Qwen cache-column semantics: research Qwen API pricing (is `cached`
      discounted vs non-cached prompt tokens? TTL? tiered rates?). The cache
      column (`read / write / hit%`, statusline_lib/qwen.py) maps
      read=cached, write=prompt-cached, hit%=cached/prompt, which is
      semantically unlike Claude's (Qwen exposes no priced write side). If
      caching isn't priced differently, the column misleads as a cost
      signal: repurpose (hit% only) or drop it. Distilled from
      QWEN-STATUSLINE-HANDOFF.md (deleted 2026-06-10; full port notes in git
      history).

## Done

- Quality gate back to green (2026-06-10): moved the nudge-hook merge
  helpers from install.py into statusline_lib/nudge_install.py so the
  verify script imports a recognized local package, clearing the
  ai-slop/hallucinated-import false positive on repo-local `import
  install` (aislop only resolves package dirs with `__init__.py`, not
  single-file modules). Also split the chained `.get(..., {})` lookup
  and ran ruff format. aislop 100/100, ruff clean, all 23 verify
  scripts pass.

- Optional native-walker integration (commit cc548d7): C++ (simdjson)
  was the bench winner at ~95ms cold, so detection was wired against
  the canonical `~/claude-walker/cpp/build/...` paths.
  `$CLAUDE_WALKER_BIN` override + PATH lookup. install.py prints which
  mode is active. Cache TTL also dropped 30s → 15s.
- Parallelize `_walk_pace_buckets` (commit 2b5e355): orjson + 8-worker
  ProcessPoolExecutor over per-session groups. 750ms → 248ms median,
  bit-exact match against the original. Cache TTL shortened 60s → 30s.
