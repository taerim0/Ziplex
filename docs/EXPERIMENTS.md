# Experiment log — model / retrieval-approach comparisons

**English** | [한국어](EXPERIMENTS_ko.md)

Ad-hoc comparisons of ways to answer questions about *this or any target
project* -- Claude Code alone vs. Ziplex MCP/Skill, different LLM
providers/models, different `aif.json` packing settings. Deliberately not
called "benchmarks": there's no fixed, repeatable test suite here, and each
round's setup (which model, which project, how much prior context the
"alone" side already had) differs enough that two rounds are only
comparable once you've actually read what each one controlled for. Never
auto-loaded (same convention as `docs/HISTORY.md`) -- open it before
deciding which approach/model to use, or before running a new comparison
round, so a new round can reuse a prior one's project/setup where that
matters.

## Why this exists

A round's *conclusion* ("Ziplex was cheaper") is worth almost nothing on its
own a few months later without the conditions that produced it -- same
model on both sides? same project, actually packed how? had Claude Code
already seen this codebase earlier in the conversation (which would make
"alone" look artificially strong)? This file exists so that context isn't
lost the way it would be if only a one-line takeaway got written down.

## How to add an entry

Copy the template block below into a new `### #N YYYY-MM-DD <short title>`
entry, fill in every field (write "n/a" rather than deleting a field you
didn't vary), and add a matching row to the summary table so the log stays
skimmable without opening every entry. Record raw numbers (token counts,
turn counts, pass/fail) in the entry -- "Ziplex was cheaper" with no number
isn't reusable later, and can't be re-checked if a later round's result
seems to disagree with it.

Keep this file and `EXPERIMENTS_ko.md` in sync -- add the same entry to
both (translated, not just copy-pasted English into the Korean file or
vice versa) rather than letting one drift ahead of the other.

## Summary table

| # | Date | Ziplex ver (commit) | Compared | Model(s) | Project | Result |
|---|------|----------------------|----------|----------|---------|--------|
| 1 | 2026-09-15 | main, pre-`29f2c1d`/`c91e5c8` (exact commit not recorded) | Ziplex Skill+CLI vs. Claude Code alone | not recorded | Ziplex's own repo (self-pack) | Baseline cheaper 3/3 rounds; real value found instead was bug-coverage complementarity (n=1, unconfirmed) |

## Entries

### Template (copy this, don't fill in above)

- **Compared**: e.g. Claude Code alone vs. Ziplex MCP server / Ziplex Skill export vs. MCP / Gemini vs. Claude summarizer
- **Ziplex version**: `git describe` or commit hash (whatever `ziplex --version` prints) -- required, since packed output shape can change between versions
- **Model(s) used**: every model id actually used on both sides, including provider. If the two sides used different models, say so explicitly -- that difference is now mixed into the result
- **Project analyzed**: project name (or an anonymized description), language, rough file count/size
- **Claude Code's prior exposure**: how much the "alone" side had already read about this project before the comparison -- had it already skimmed the whole codebase earlier in the same conversation, or did it start from a genuinely fresh context? If this differs between rounds, "alone" can look artificially stronger or weaker than it really is -- always record it
- **Ziplex packing state**: `pack` command options used (`--auto`/`--no-llm`/`--lang`/`--no-cache`/etc.), whether cache was reused, `aif.json`/`detail.json` size (tokens), average confidence
- **Metrics measured**: what was counted and how -- input/output tokens, turn count, answer correctness, wall-clock time. Note how each number was actually measured (e.g. "via `ziplex tokens`" / "real usage from the Anthropic console")
- **Result**: numbers, not just which side "won" -- include the size of the gap
- **Notes**: only what the fields above don't capture -- an outlier specific to this round, anything that could affect reproducibility (network retries, 503s, etc.), what to do differently next round

---

### #1 2026-09-15 — Ziplex Skill/CLI vs. Claude Code alone, token efficiency

- **Compared**: Condition A "Ziplex-assisted" -- told to use the pre-exported Claude Skill (`.claude/skills/ziplex/`) and the `ziplex` CLI as primary investigation tools. Condition B "Claude Code alone" -- explicitly forbidden from touching the ziplex skill, the `ziplex` CLI, or `.ziplex/`; pure Grep/Glob/Read/Edit only.
- **Ziplex version**: main, at the time of the experiment, before the fix commits it produced (`29f2c1d`, `c91e5c8`) were merged -- exact commit hash wasn't recorded (record `ziplex --version`/the commit hash from now on)
- **Model(s) used**: not recorded -- whatever the session's default subagent model was at the time; the exact model id can't be recovered retroactively
- **Project analyzed**: Ziplex's own repo (self-pack)
- **Claude Code's prior exposure**: a genuinely fresh `general-purpose` subagent per round (not a fork -- forks inherit conversation context, which would leak the answer), each in its own isolated `git worktree`. Rounds 2/3 were blind (no file/function names given at all); **round 1's design was flawed** -- it named the buggy file directly in the prompt, so it only measured "cost once you already know where to look," and was invalidated after the fact.
- **Ziplex packing state**: Condition A used an already-exported Claude Skill (including `references/relationships.md`) as-is -- no fresh pack for this round, options/cache state not recorded
- **Metrics measured**: the harness's own subagent-completion `<usage>` block -- `subagent_tokens`/`tool_uses`/`duration_ms`. Never self-reported by the agent. Every "fix complete" claim was only trusted after the coordinating session independently re-verified it (re-ran the logic, re-ran the tests).
- **Result**:
  - Round 1 (invalid -- file name given upfront): A 174,169 tok/40 calls/347s vs. B 129,872 tok/27 calls/368s
  - Round 2 (blind): A 181,074 tok/40 calls/391s vs. B 143,068 tok/24 calls/247s -- each found a **different** real bug (no overlap, confirmed directly)
  - Round 3 (blind, 2026-09-15): A 184,908 tok/51 calls/804s vs. B 175,412 tok/34 calls/555s -- both found the same bug, but **B's fix was incomplete** (its own tests passed but missed a more general case); A's fix was the actually-correct, general one
  - Overall: baseline (alone) was cheaper in 3/3 rounds on token cost -- the "efficiency" hypothesis is falsified for this task class (debugging/root-causing)
- **Notes**:
  - A different value signal turned up instead: pre-aggregated structured data (a relationships-graph doc) may make certain bug shapes (structural anomalies) easier to spot by inspection -- a "coverage complementarity" hypothesis, n=1, unconfirmed
  - 3 real bugs found/fixed during this experiment (`extract/code/languages.py`'s `from . import X` resolution bug, `file/relationship.py`'s cross-language same-stem collision, `resolve_dependency()`'s unchecked absolute-import path) -- all merged to main
  - **Most important lesson**: a subagent's own "tests pass" is not evidence a fix is complete -- both round 3's incomplete fix from B and a regression in the already-merged round-1 fix itself (an unconditional bogus `"."`/`".."` dependency entry) survived the full test suite passing, and were only caught by the coordinator's independent re-verification
  - Found that `isolation: "worktree"` didn't pick up a branch switch made in the coordinating session's own working directory right before launching (an early round-3 design flaw) -- when a worktree-isolated subagent's base ref actually matters, verify it directly afterward rather than assuming
