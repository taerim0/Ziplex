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
| 2 | 2026-09-16 | main `57a412a` | Ziplex-packed context+CLI vs. Claude Code alone | claude-sonnet-5 (both sides) | scrapy (real external repo, 502 `.py`/696 total files) | Baseline cheaper (63,876 vs. 65,565 tok) and faster (215s vs. 263s); both found the same real bug equally fast, but the Ziplex-assisted fix had a real regression baseline's fix didn't -- caught only by running the target project's own test suite |

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

---

### #2 2026-09-16 — Ziplex-packed context vs. Claude Code alone, on a real large external project

- **Compared**: Condition A "Ziplex-assisted" -- given an already-generated Ziplex pack (`aif.json`/`detail.json`/`cache.json`) for the target project plus the `ziplex` CLI, told to use both as the primary way to understand the codebase before falling back to raw Grep/Read. Condition B "Claude Code alone" -- explicitly told not to use the `ziplex` CLI or any pre-packed file; pure Grep/Glob/Read/Edit/Bash. (No Claude Skill this round -- see the note on that below.)
- **Ziplex version**: main @ `57a412a` (`ziplex 0.5.0`). Note: the pack run for this round is what surfaced and forced a same-day fix (`57a412a` itself) -- see notes.
- **Model(s) used**: `claude-sonnet-5` on both sides (explicit `model: "sonnet"` on both subagent launches, so this is pinned and recorded, unlike round 1's gap)
- **Project analyzed**: [scrapy/scrapy](https://github.com/scrapy/scrapy) (real, actively-maintained open-source Python web-scraping framework), cloned at commit `86bec33cd3e6311b8481eac7d02e3ab0ec06f394` (2026-09-15). 502 `.py` files, 696 files total, ~8.7MB. Chosen deliberately over Ziplex's own repo (round #1) to test whether the "baseline is cheaper" finding was an artifact of testing on a codebase small enough for grep alone to cover cheaply -- picked by the user's own explicit choice of "well-known large open-source repo" over reusing an already-packed real project, accepting the tradeoff that a well-known repo may be partially present in the model's training data (see notes).
- **Claude Code's prior exposure**: two genuinely fresh `general-purpose` subagents (not forks), each given its own isolated copy of the clone (`scrapy_A`/`scrapy_B`, plain directory copies, not git worktrees -- the agents edit files outside the Ziplex repo entirely, so Ziplex-repo worktree isolation doesn't apply here). Neither subagent had seen this exact clone before. Both were explicitly instructed not to rely on general/training knowledge of Scrapy's internals without verifying it against the real code in front of them, to partially mitigate the training-data-familiarity risk noted above (this instruction can reduce but not eliminate that risk).
- **Ziplex packing state**: `ziplex pack <scrapy_A> --auto --auto-correct` (real Gemini calls, `gemini-3.5-flash`, default output dir `.ziplex/`) -- required two resumes from checkpoint (see notes) before completing. Final pack: 665 files summarized, token analysis reported `GPT-4o: 1,148,518 → 42,122 (96.3% reduction)` / `Claude (approx): 1,200,250 → 39,927 (96.7% reduction)`. This packing happened *before* either subagent was launched, so it is not counted in Condition A's measured cost -- same convention as entry #1.
- **Metrics measured**: harness's own subagent-completion `<usage>` block (`subagent_tokens`/`tool_uses`/`duration_ms`), never self-reported. Correctness was independently re-verified by the coordinating session: (1) diffed each copy's change against the pristine clone, (2) ran a fresh verification script (not either agent's own repro) covering 5 cases including two neither agent's own script covered, (3) ran the target project's own real, pre-existing test files (`tests/test_utils_response.py`, `tests/test_http_response_text.py`, `tests/test_linkextractors.py`) against both patched copies and the pristine baseline.
- **Result**:
  - Condition A (Ziplex-assisted): 65,565 tok / 16 tool calls / 262.6s (~4.4 min)
  - Condition B (Claude Code alone): 63,876 tok / 19 tool calls / 214.9s (~3.6 min)
  - Baseline was both cheaper (**~2.6% fewer tokens**) and faster (**~18% less wall-clock time**) despite making *more* tool calls (19 vs. 16) -- suggests A's `ziplex` CLI subprocess calls were individually costlier/slower than B's native Grep/Read for this task shape, not that A did less work.
  - Both conditions found the exact same root cause (the same real, still-open bug: [scrapy/scrapy#3017](https://github.com/scrapy/scrapy/issues/3017), filed 2017 -- `get_base_url()`/`get_meta_refresh()` in `scrapy/utils/response.py` hard-truncate the response body to the first 4096 characters before searching for a `<base>`/meta-refresh tag) equally fast. Condition A found it via one `ziplex search` call that landed directly on the truncation line; Condition B found it via a repo-wide Grep for `base href|<base|_baseurl|...` that also landed on it almost immediately. **Unlike round 1's rounds 2/3, the two conditions did not diverge on which bug they found this time** -- this particular bug was locatable fast either way.
  - **Correctness diverged, though**: independent verification (the coordinator's own 5-case script, covering both `get_base_url` and `get_meta_refresh`, a document with no `</head>` at all, a regression check, and a no-`<base>`-tag fallback check) showed BOTH fixes passing all 5 cases. But running the *target project's own pre-existing test suite* against each copy found that **Condition A's fix broke a real, existing test** (`tests/test_utils_response.py::test_get_meta_refresh`, specifically a malformed-HTML fixture where a `<meta>` tag sits inside `<noscript>` and a literal `</head>` string appears before the noscript's real closing tag) -- A's fix truncates the search text at the first `</head>` match, which in that fixture cuts off the closing `</noscript>` tag before `w3lib`'s `ignore_tags` stripping logic ever sees it, so the meta tag that should be ignored gets picked up instead. Condition B's fix (removing the 4096-char truncation entirely rather than replacing it with a different boundary) has no such failure mode and passed the full existing suite (26/26 in that file, 277/277 across the two broader files) with zero regressions. The pristine baseline also passes all 26 (none of the existing fixtures are large enough to trigger the original bug), confirming this is a regression A's fix introduced, not a pre-existing flaky test.
- **Notes**:
  - **A real Ziplex bug was found and fixed as a direct side effect of just trying to pack a 500+ file real project for this round**: `packager._extract_rules()` fed every collected file's signatures into `analyze_rules()`'s prompt with no size cap at all (unlike `_build_architecture_summary()`'s existing `MAX_ARCHITECTURE_FILES` cap for a *different* prompt). For this project the uncapped `signatures_map` repr alone ran to ~94k tokens, which the model could not return as a complete JSON response -- and because this "succeeded" at the HTTP level every time (not a 429/503 `generate()` would retry), non-interactive `pack()` had no way to tell it apart from a real, permanent failure, so it checkpointed and exited deterministically on *every* attempt, twice, before this was diagnosed and fixed (commit `57a412a`, same day, 897/897 tests passing after the fix). Without this fix, Condition A's pre-packed context for this round would not exist at all -- this is exactly the kind of large-real-project gap round #1 (self-pack on Ziplex's own ~115-file repo) could never have surfaced.
  - **Reinforces round #1's "tests pass ≠ fix complete" lesson, but this time on the higher-effort side**: Condition A's fix was the more elaborate of the two (a new regex + helper function, explicitly reasoned about a `</head>`-boundary rationale, and included 3 self-described "edge case" checks) and still shipped a real regression; Condition B's fix was the simpler one (delete the truncation, rely on existing per-response caching to keep it cheap) and had none. Neither agent's own testing caught A's regression -- it surfaced only when the coordinator ran the *target project's own* pre-existing test suite against the patched code, not either agent's self-written repro script. "More thorough-looking self-reported verification" was not a reliable signal of actual correctness here.
  - **Methodological gap from the plan**: Condition A was designed to use "the pre-exported Claude Skill" the same way entry #1 did, but Claude Code's Skill auto-discovery is scoped to the *main session's* project root (this Ziplex repo), not a subagent's own working directory in an unrelated external path -- there was no way to make a "scrapy" skill auto-available to a subagent operating outside this repo. Substituted with direct `aif.json`/`detail.json` file paths + the `ziplex` CLI instead, explained to the agent as its primary tool. This is arguably a more direct test of the underlying packed data's usefulness (no skill-packaging/discovery layer in between), but it is a real deviation from entry #1's design worth knowing about before comparing the two entries too literally.
  - **Training-data-familiarity risk, accepted not eliminated**: scrapy is a well-known library; either subagent could in principle answer from memorized training data rather than genuinely investigating this exact copy. Both were explicitly instructed to verify claims against the real code rather than trust recollection, and the recorded tool-call counts (16/19) plus the specific request to inspect the real `w3lib` source (which Condition B did) suggest real investigation happened rather than a memorized answer -- but this risk is mitigated, not ruled out, and is exactly the tradeoff the "well-known OSS repo" choice accepted over reusing an already-packed private project.
  - n=1 on this bug/project, same caveat as entry #1 -- this establishes that for *this* bug in *this* project, baseline was cheaper, faster, and (via an unrelated solution-design choice, not a tooling-caused difference) more correct. It does not establish that Ziplex-assisted investigation is generally slower, costlier, or lower-quality than baseline.
