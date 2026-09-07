# Retrieval-quality pilot: raw-repo grep vs. Ziplex-packed context (2026-09-07)

## Why this exists

Every measurement Ziplex has published about itself so far is a **compression ratio** (e.g. "348,232 → 6,997 tokens"). That answers "how much smaller is the context," not "does an AI actually find the right code faster/more reliably when it starts from Ziplex's packed output instead of the raw repo." Three independent conversations with an outside model reviewing this project (logged in this session and the prior one) converged on the same critique unprompted: build a real retrieval/task-success benchmark, not just another compression number.

This is a **pilot**, not the full benchmark: one project, 5 questions, no scoring rubric beyond "did it name the right file." It exists to (a) prove the methodology actually works before investing in a bigger version, and (b) get a first real number instead of continuing to argue about this in the abstract.

## Method

**Project**: `testfiles/game-mod-project` — a 12-file real Minecraft/Forge mod fixture already packed with a **real** (not mock) Gemini run in an earlier session (`result/game-mod-project.json` + `.detail.json`), so the packed summaries reflect genuine LLM output, not a placeholder.

**Two conditions**, both run as fresh, isolated subagents (no shared context with each other or with whoever wrote the questions) using Claude's own `Explore` agent:

- **Raw**: given only `testfiles/game-mod-project/` and told to use Glob/Grep/Read to find the answer, explicitly forbidden from touching anything under `result/`. Simulates an agent working on a repo with no Ziplex pack.
- **Ziplex**: given only `result/game-mod-project.json`, told to answer from that alone if possible, and permitted to additionally read the sibling `result/game-mod-project.detail.json` (one file's compressed body) only if it genuinely couldn't answer from the summary. Simulates the "packed context first, escalate to detail on demand" model Ziplex's own README describes.

Each subagent reported its answer, a reasoning line, and its own tool-call count. **Correction (2026-09-07, same-day code review):** those self-reported counts were originally taken at face value. A follow-up review cross-checked them against the harness's own independent `tool_uses` telemetry (attached to each subagent's completion event, not something the subagent itself could see or influence) and found the self-reports undercounted by 1 in 3 of the 5 raw-condition runs (every Ziplex-condition self-report matched exactly — plausible, since 1-2 total calls is harder to miscount than 4-8). The table and numbers below use the harness-verified counts, not the original self-reports; see the Verification section at the bottom for the full correction.

**Ground truth** was set by reading the actual source files directly before running any subagent (not from memory of the project). One question (Q5) turned out to have a genuinely ambiguous ground truth — see below — and is excluded from the accuracy score, though its cost numbers are still reported.

## Questions and results

| # | Question | Ground truth | Raw: correct? | Raw tool calls (harness-verified) | Ziplex: correct? | Ziplex tool calls | Ziplex needed detail.json? |
|---|---|---|---|---|---|---|---|
| Q1 | Where is the ruby-ore extra-drop *chance* actually configured (the tunable number)? | `ModConfig.java` | ✅ | 5 | ✅ | 2 | yes |
| Q2 | Which file/function decides whether an item counts as a "ruby tool"? | `ModItems.java` | ✅ | 4 | ✅ | 1 | no |
| Q3 | Which single file to edit to change ruby-sword-kill behavior? | `PlayerEventHandler.java` | ✅ | 6 | ✅ | 1 | no |
| Q4 | Which file controls the ruby ore block's bonus XP yield? | `RubyOreBlock.java` | ✅ | 4 | ✅ | 1 | no |
| Q5 | Which two files form a circular/mutual import? | *(see below — excluded)* | (found all 3, see below) | 8 | (found all 3, see below) | 2 | yes |

**Accuracy (Q1–Q4, n=4, single-answer questions): Raw 4/4 (100%), Ziplex 4/4 (100%).** Not a surprise at this project size — a 12-file repo is small enough that a competent grep-based agent doesn't get lost. This pilot's project was too small to show an accuracy gap; that's a real limitation, not a null result to hide (see "What this pilot doesn't show" below).

**Tool-call cost (the number that actually moved): average 4.75 calls/question raw vs. 1.25 calls/question Ziplex on Q1–Q4 — a ~74% reduction at equal accuracy** (corrected from an original ~69%/4.0-calls figure that used the subagents' own undercounted self-reports — see the note above). Three of four Ziplex answers (Q2/Q3/Q4) were resolved from the packed summary file alone, zero escalation to `detail.json` needed. Q1 needed one `detail.json` read to confirm the exact tunable value (`0.15`) rather than just knowing which file owned it.

**Q5 — a real methodology finding, not a Ziplex result.** The question assumed (from this project's own prior session history) a single "the" circular dependency between `ModItems.java` and `CoolMod.java`. Both subagents, independently, found that this project actually has **three** structurally-identical mutual-import pairs, all centered on `CoolMod.java` (↔ `ModItems.java`, ↔ `ModBlocks.java`, ↔ `PlayerEventHandler.java`) — the classic Forge "registry class needs the mod's `MOD_ID` back" pattern repeated three times. Both conditions correctly discovered the *full* graph structure and, without coordinating, both independently picked the same file (`PlayerEventHandler.java`) as "the clearest" pair to report. The question itself was mis-specified (my own ground-truth error, sourced from memory rather than re-verified against the code), not a case either condition got "wrong" — excluded from the accuracy tally for that reason, but its cost numbers (raw: 8 tool calls / 7 files opened, i.e. it read every Java file in the project; Ziplex: 2 reads) are kept since both conditions answered the same real underlying question correctly.

## What this actually shows

- At equal correctness, Ziplex's packed context answered targeted "where do I look" questions with **roughly 1/4 the retrieval steps** of grepping the raw repo, on this project.
- **Most of that saving came from never needing to escalate past the summary file at all** — 3 of 5 questions never touched `detail.json`, i.e. the packed `aif.json` alone was sufficient, exactly the "cheap first pass, escalate only when needed" model the README claims.
- The one place a real edge in *correctness* (not just cost) showed up wasn't between raw-vs-Ziplex — it was in **how completely each condition mapped the actual relationship structure** (Q5): both got there, but the Ziplex condition did it by reading a `relationships` field meant for exactly this, while the raw condition had to open every file in the project to reconstruct the same graph by hand.

## What this pilot doesn't show (and shouldn't be read as showing)

- **n=5 questions, 1 project, 1 model, 1 run each — not statistically meaningful on its own.** Read this as "the methodology works and produced one plausible data point," not as a validated result.
- **12 files is too small to stress raw-repo retrieval.** The real claim to test is what happens at 100+ or 1,000+ files, where a raw-grep agent plausibly starts missing the right file or needing many more exploratory reads — this pilot's project can't show that gap because it's too small for the failure mode to appear.
- **Tool-call count is a proxy, not a real cost/latency measurement.** It doesn't capture actual token counts per call, wall-clock time, or the fact that a `Read` on a full raw Java file costs more tokens than a `Read` on one packed summary line even when both count as "1 tool call."
- **Not blinded end-to-end**: the ground truth was authored by the same person (me) who designed the questions, with knowledge of the project. The subagents that actually answered were cold/isolated, which is the part that matters for validity, but question *selection* itself wasn't adversarial or independently audited.
- **Asymmetric instructions, found on code review**: the Ziplex-condition prompt explicitly told the subagent to prefer answering from the summary alone and escalate to `detail.json` "only if you genuinely cannot answer confidently" — an economy instruction the raw-condition prompt never gave (it was just told to explore/grep/read "as needed"). Some of the tool-call gap could reflect that asymmetry rather than a purely inherent cost difference between the two context shapes. A tighter version of this pilot would give both conditions the same "be as economical as possible" instruction and let the actual context shape do the differentiating.

## Verification pass (2026-09-07, same day, on request)

Two checks, run after the fact rather than assumed:

1. **Fixture freshness**: `ziplex freshness testfiles/game-mod-project result/game-mod-project.cache.json` → `✅ 최신 상태 — 변경된 파일 없음`. The packed data this pilot used genuinely matches the current source, not a stale snapshot.
2. **Real (non-mock) re-pack + independent recomputation**: re-ran `ziplex pack testfiles/game-mod-project --auto --auto-correct --no-cache` against the real Gemini API (not `MockProvider`) to confirm both the base pipeline and the same-day folder-confidence feature (see the roadmap memory's "Folder-summary confidence + triage shipped" entry) work end to end against genuine LLM output, not just the test suite's mocked cases. Every file and folder in the fresh output scored `confidence: 1.0` — independently re-derived by extracting `ModItems.java`'s real signatures and calling `confidence.estimate_confidence()` directly against the freshly-generated summary, which matched the stored value exactly. All real fixtures currently in this repo (`game-mod-project`, and the 47-file `amdc-develop`) happen to have no low-confidence files at all, so this didn't stress-test the folder-level *averaging* arithmetic with genuine variance — that part is covered by `tests/test_folder_summary.py`'s synthetic-data unit tests instead, not by a real pack.

## Next step, if this is worth scaling up

A real version of this needs: 3-5 projects across a size range (not just 12-file fixtures — something in the hundreds of files, ideally including one where the raw-grep condition can actually fail), 20-30 questions with pre-verified single-answer ground truth, matched economy instructions across conditions, and ideally a third condition (a native coding agent's own built-in repo exploration, not just raw grep) to test the actual competitive claim from the GPT conversations — that Ziplex needs to prove itself against what agents already do on their own, not just against blind grep. Not started; this pilot was scoped deliberately small to validate the method first.
