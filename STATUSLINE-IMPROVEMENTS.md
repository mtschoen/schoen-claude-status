# Statusline Improvement Ideas

Working notes for improvements to the user's custom Claude Code statusline + walker script. Captured from the 2026-06-01 brainstorming session for the team talk. Separate from `SCRATCH-NOTES.md` because these are tooling ideas, not deck content (though both could surface in the talk as screenshots or as "things I built to make the costs visible to myself").

**Current statusline shape** (for reference):
```
opus[1m] | 265.7K / 1.00M (26.6%) | 11.98M / 428.1K / 96% hit | $15.90 +$1.16~
```

Decoded: `model[context-tier] | context fill / window (% used) | cumulative cache reads / cache writes / hit rate | session cost so far + estimated current-turn cost`

---

## Idea 1: Per-turn cost decomposition — explicit read vs write split

**Problem**: The current statusline shows aggregate cache reads, cache writes, and a single total cost. The user can see *that* writes happened but not *what they cost relative to reads* — and cache writes are 1.25× the price of input tokens (5-min TTL) or 2× (1-hour beta), while cache reads are 0.1×. The ~12.5× price ratio between cache-write and cache-read tokens is invisible in the current display.

**Proposal**: Add an explicit cost-split for reads vs writes, e.g.:
```
$2.10r / $4.85w  →  $6.95 total cache cost (28% reads / 72% writes)
```

Or compressed:
```
$15.90 [$2.10r/$4.85w/$8.95base]
```

**Implementation**: The walker script already accumulates per-turn token counts. Add per-turn cost computation, factoring:
- Cache reads at 0.1× input rate
- Cache writes at 1.25× input rate (5-min TTL, default) or 2× (1-hour beta)
- 2× over-200K-threshold for legacy models that still have that pricing tier (Sonnet 4.5 / 4 1M beta — *not* Opus 4.7 or Sonnet 4.6, where the threshold was eliminated 2026-03-13)
- Output tokens at the output rate (~5× input)

Accumulate across all turns in the session; display split.

**Why it would land** (also captured in `SCRATCH-NOTES.md` supplementary seed #2):
- The abstract "writes are 1.25× more expensive than reads" claim from §02 is academic.
- The visible per-session breakdown (*"70% of your bill is cache writes"*) is immediately actionable — users would naturally start asking *"why are my cache writes so high? what's invalidating my prefix?"* without having to be told to.
- Pairs with the homelab live demo in the talk as a *second* observability moment — concrete cost-state visible in the terminal, in real time. Could become a screenshot slide if not a live element.

**Status**: SHIPPED 2026-06-02 in PR #9 (commit `38be07e`). The cache $-split now renders read/write per-figure costs via `format_cache()` in `statusline_lib/costfmt.py`. Demo-ready for the talk.

---

## Idea 2: TTL invalidation counter — loud signal when cache gets evicted

**Problem**: The 5-minute TTL trap is the single biggest invisible cost on long workflows (silently dropped from 1-hour on 2026-03-06, [claude-code#46829](https://github.com/anthropics/claude-code/issues/46829)). When a session goes idle past the TTL, the entire cached prefix is evicted and the next turn pays a full cache-write penalty. **The user has no real-time signal that this just happened** — they only see the aggregate cost go up.

**Proposal**: Count TTL invalidations and surface them prominently.

**Detection rule**: *Any prompt after the initial prompt of the session that was 100% cache write (i.e., 0% hit rate on the input prefix).*

Reasoning: after turn 1, the cached prefix exists. If a subsequent turn arrives with 0% cache hit on the input side, the cache must have been evicted — TTL expiry being the most common cause (other causes: compaction, tool-result reordering bug ([claude-code#42338](https://github.com/anthropics/claude-code/issues/42338)), session resume past TTL).

**Display**: Loud signal. Options:
- Counter: `TTL: 3 evictions` (numerical, persistent)
- Color: red/orange when an eviction just happened on the most recent turn
- Icon: ⚠️ or 🔥 near the counter when the eviction count increases
- Per-eviction cost note: `TTL: 3 (~$0.85 wasted)` — multiplying eviction count by typical cache-write penalty per eviction, gives a concrete "this is what your neglect cost you"

**Why it would land**:
- The penalty for TTL invalidation is real and meaningful — a full cache-write on what should have been a 0.1× cache-read can add a noticeable dollar amount per eviction on a heavy session.
- Currently invisible: the user only sees the aggregate cost creep up; can't attribute it to specific behavior (walking away from the session).
- A loud signal would let the user *self-correct* the behavior in real time ("oh, I just got back from a meeting and the cache evicted — let me batch my work so this doesn't happen again") instead of discovering the cost after the session ends.
- Pairs with the §06 Session Discipline material in the talk — the "don't let the cache go cold" rule of thumb (Dump #10's cache-write-avoidance subsection) becomes much more actionable when you can *see* the cost of failing to follow it.

**Edge cases to handle**:
- First turn of a session is always a 100% cache write — exclude from the counter (it's the establishing turn, not an eviction).
- Compaction events also produce 100% cache writes on the next turn — could be distinguished by whether the user explicitly ran `/compact`, but might be fine to lump them all under "TTL eviction" as a proxy for "you reset your cache."
- Session resume (`--resume`, `--continue`): the first turn of the resumed session will be 100% cache write if resumed past TTL. Counts as a TTL eviction — semantically correct.
- Tool-result-reordering cache-invalidation bug ([claude-code#42338](https://github.com/anthropics/claude-code/issues/42338)) would also trigger this counter — that's actually a *feature*, not a bug, because it surfaces a real cost the user is paying that's currently invisible.

**Status**: SHIPPED 2026-06-02 in PR #9 (commit `38be07e`). `format_ttl()` in `statusline_lib/costfmt.py` renders the loud red eviction counter; the idle-gap detection was tightened in commit `201d1e2`. Demo-ready for the talk.

---

## Cross-references

- Both ideas originally surfaced in `SCRATCH-NOTES.md` (supplementary seed #2, "long session ≠ bad — cost depends on what the session is doing").
- Related §02 / §04 material: cache pricing mechanics, TTL trap, prefix invalidation bugs.
- Related deck framing: "what gets measured gets managed" — these are both observability improvements that make abstract cost rules visible and actionable.
