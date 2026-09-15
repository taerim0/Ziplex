# AGENTS.md

This file guides AI coding agents (Claude Code, and any tool following the AGENTS.md convention) in this repository. `CLAUDE.md` at each path below is a one-line `@AGENTS.md` import, so Claude Code reads identical content — see [`docs/team/agents-md.md`](docs/team/agents-md.md). Kept lean as a current-state map, not a change log — see `docs/HISTORY.md` for the decision/bug-fix history behind anything below. That file is never auto-loaded; open it only when the "why" actually matters for what you're about to change.

Directory-scoped detail lives in nested AGENTS.md files, auto-loaded when working in that subtree (via each directory's own `CLAUDE.md` importing its sibling `AGENTS.md`):
- `src/ziplex/AGENTS.md` — core pipeline modules directly under `src/ziplex/*.py`: LLM layer, checkpointing, summarization, orchestration (`packager.py`), the pure editing API's callers, relationship enrichment, freshness/confidence/search, MCP query layer, per-user/per-project config, skill-export/MCP distribution channels.
- `src/ziplex/extract/code/AGENTS.md` — Tree-sitter code compression/extraction, one language per bullet (Python/Java/TypeScript/JavaScript/Lua/GDScript/Go/C++/Rust/C#/PHP/Ruby/Bash).
- `src/ziplex/extract/text/AGENTS.md` — non-Tree-sitter text compressors (json/yaml/markdown/txt/dockerfile).
- `src/ziplex/file/AGENTS.md` — file collection, security scanning, dependency-graph API.
- `src/ziplex/gui/AGENTS.md` — local GUI (Flask + pywebview backend, ES-module frontend).

Packaging: a real installable package (`pyproject.toml`, src-layout — `src/ziplex/` is the actual `ziplex` package). Internal imports are package-relative; nothing under `src/ziplex/` imports a sibling module by bare name. `pip install -e .` registers three console scripts: `ziplex`, `ziplex-gui`, `ziplex-mcp`.

## What this is

Local project → `aif.json`: a token-reduced context format an AI reads instead of raw files. Pipeline: Tree-sitter compression/extraction → Gemini summarization → optional human correction. `aif.json` carries only summaries + relationships, never full per-file code — shape at the bottom of this file.

## Commands

Setup (Windows, matches the existing `venv/` in this repo):
```
venv\Scripts\activate
pip install -e .                 # editable install: reads pyproject.toml, registers ziplex/ziplex-gui/ziplex-mcp
pip install -e ".[dev]"          # + pytest
```
`pip install -r requirement.txt` (no "s") is a lighter-weight alternative that skips packaging (no console scripts) — subcommands then run as `python src/cli.py ...`.

Requires a `.env` with `GEMINI_API_KEY=...` (via `python-dotenv`, `src/ziplex/llm.py`); optionally `GEMINI_MODEL=...` to override the default model.

Run any subcommand from repo root (`ziplex ...` after `pip install -e .`; `python src/cli.py ...` works either way):
```
ziplex pack <project_path>                              # full pipeline, interactive file selection + correction
ziplex pack <project_path> --auto                       # skip interactive selection, include all safe files
ziplex pack <project_path> --auto-correct                # skip interactive correction, auto-accept LLM output
ziplex pack <project_path> --auto --auto-correct         # non-interactive, for CI/scripted use
ziplex pack <project_path> --no-cache                   # force-resummarize every file
ziplex pack <project_path> -o out.json                  # custom output path (+ out.detail.json/out.cache.json)
ziplex pack <project_path> --include "src/**/*.py,*.md" --ignore "**/*.generated.*"  # one-off scope, on top of .ziplex.json
ziplex pack <project_path> --max-tokens 50000 --max-tokens-model GPT-4o  # CI guard: non-zero exit if payload exceeds N tokens
ziplex pack <project_path> --auto --no-llm               # no GEMINI_API_KEY/network -- structural summaries only
ziplex pack <project_path> --lang ko                     # packed CONTENT language (summaries/rules/AI guide) -- en (default) or ko

ziplex init <project_path>               # scaffold .ziplex.json (include/ignore patterns)
ziplex collect <project_path>            # just file collection + security scan
ziplex tokens <project_path>             # token count, before/after compression
ziplex tree <project_path>                # dependency tree only
ziplex search <project_path> <pattern> --context 2 --ignore-case
ziplex detail <name>.detail.json <file-key> --start 10 --end 40   # partial read of one file's compressed body
ziplex freshness <project_path> <name>.cache.json                # hash-check aif.json against disk, no LLM calls
ziplex skill <name>.json                                          # export as a Claude Agent Skill (.claude/skills/<slug>/)
ziplex skill <name>.json --project <project_path>                  # + a free freshness warning if the project has drifted since packing
ziplex link <name>.json <file> <target>                            # one-shot: add a dependency edge to an already-saved aif.json's relationships
ziplex unlink <name>.json <file> <target>                          # one-shot: remove that edge
ziplex summary <name>.json <file> "<text>"                         # one-shot: fix an already-saved file's summary, no re-pack
ziplex summary <name>.json <folder> "<text>" --folder              # same, for a folder summary (aif["folders"])
ziplex signatures|dependencies|api|compress|debug <single_file>
ziplex --version                                                   # print installed version and exit (also ziplex-gui/ziplex-mcp --version)
ziplex settings                                                    # print ~/.ziplex/settings.json -- CLI counterpart of the GUI Options page
ziplex settings set <key> <value>                                  # e.g. `ziplex settings set llm_provider openai` -- key must be one of settings.EDITABLE_FIELDS
ziplex checkpoint                                                  # list leftover checkpoint/*.json files (project name, pending file count, size, saved-at)
ziplex checkpoint clean <project_path>                             # delete just that project's checkpoint
ziplex checkpoint clean --all                                      # delete every checkpoint
ziplex doctor [project_path]                                       # environment check -- Python version, active LLM provider/model/API key, secretlint, leftover checkpoints (no LLM call); project_path adds .env/git-repo checks
```
(`<project_path>` is *any* target project being packed/inspected — `--include "src/**/*.py"` is that target project's own `src/`, unrelated to Ziplex's own.)

MCP server (exposes an already-packed project's `aif.json`/`detail.json`, read-only):
```
ziplex-mcp                                  # run directly, stdio transport
claude mcp add ziplex -- ziplex-mcp         # register with Claude Code
```

Local GUI (browse an already-packed project, or pack one interactively — see `src/ziplex/gui/AGENTS.md`):
```
ziplex-gui                                            # native window (pywebview)
ziplex-gui --aif out.json --project ./your-project    # prefill the landing page
ziplex-gui --no-window                                # plain browser tab instead of a pywebview window
```
`--auto --auto-correct` makes `pack` fully non-interactive: `checkpoint.py`'s `handle_llm_failure()`/`resume_checkpoint_choice()` both take an `interactive` flag (from `--auto-correct`) and skip straight to their non-interactive default (checkpoint-and-exit; always-resume) instead of calling `input()` against a closed stdin.

Tests (`tests/`, pytest via `pytest.ini` which puts `src/` on `pythonpath`):
```
pip install -r requirement-dev.txt   # or: pip install -e ".[dev]"
pytest
```
Covers the deterministic, non-LLM logic: compressors, extractor, `file/collector.py`'s filtering, `config.py`, `file/relationship.py`, `edits.py`, `search.py`, `freshness.py`, `confidence.py`, `skill_export.py`, `text_references.py`, `tech_stack.py`, `cli.py`'s argument-level wiring (via `monkeypatch`-injected fakes), `summarizer.py`, `tokenizer.py`, `checkpoint.py`'s non-interactive paths. `tests/test_pack_integration.py` runs the full `pack()` pipeline end to end against `llm.MockProvider`. `tests/test_mcp_server.py`/`tests/gui/test_pack_service.py`/`tests/gui/test_gui_server.py` cover the MCP and GUI layers on top of that. Nothing in the suite calls Gemini or needs `GEMINI_API_KEY`. `testfiles/` holds sample input for manual CLI runs, not part of the suite.

`tests/` mirrors `src/ziplex/`'s own subpackage layout past the flat core-pipeline modules: `tests/extract/code/`, `tests/extract/text/`, `tests/file/`, `tests/gui/` hold that subpackage's tests (e.g. `tests/file/test_relationship.py` ↔ `src/ziplex/file/relationship.py`), everything testing a root-level `src/ziplex/*.py` module (`checkpoint.py`, `packager.py`, `llm.py`, ...) stays flat directly under `tests/`. `conftest.py`'s autouse fixture applies to every test regardless of depth. Keep new test files following whichever side of that split their target module is on.

Manual smoke test against a real project without waiting on Gemini: `LLM_PROVIDER=mock ziplex pack <project_path> --auto --auto-correct` — network-free, under a second.

## Architecture

`cli.py` is a thin argparse dispatcher (`_build_parser()` + one `_cmd_*` function per subcommand, dispatched via a `_COMMANDS` dict) — all real logic lives in the modules it calls. `pack` runs the full pipeline end to end. Steps 3-7 and 9 (the modules living directly under `src/ziplex/*.py`) are covered in full detail in **`src/ziplex/AGENTS.md`** — read that file before touching any of them; this list is only a map.

1. **`file/collector.py` → `file/scanner.py` → `file/selector.py`** — collect, security-scan, and (unless `--auto`) interactively select files. Detail: `src/ziplex/file/AGENTS.md`.
2. **`extract/code/*`** + **`extract/text/*`** — Tree-sitter signature/dependency/API extraction and body-stripping compression for code; structure-preserving compression for JSON/YAML/Markdown/plain text/Dockerfile. Detail: `src/ziplex/extract/code/AGENTS.md` / `src/ziplex/extract/text/AGENTS.md`.
3. **`tokenizer.py`** — counts tokens per model, before vs. after compression and before vs. payload. Detail: `src/ziplex/AGENTS.md`.
4. **`llm.py`** — the provider layer (Gemini/OpenAI/Claude/Mock), retry/backoff, per-language prompts. Detail: `src/ziplex/AGENTS.md`.
5. **`checkpoint.py`** — `pack()`'s checkpoint/resume system. Detail: `src/ziplex/AGENTS.md`.
6. **`summarizer.py`** — batched LLM summary generation, plus the `use_llm=False` structural fallback. Detail: `src/ziplex/AGENTS.md`.
7. **`packager.py`** (`pack()`) — orchestrates 1–6 plus `text_references.py`/`folder_summary.py`; the pipeline stitcher. Detail: `src/ziplex/AGENTS.md`.
8. **`edits.py`** + **`file/relationship.py`** — the pure, I/O-free editing API and dependency-graph ops (`build_tree`/`move_file`/`has_cycle`). Detail: `src/ziplex/file/AGENTS.md` (`has_cycle`'s traversal direction is easy to get backwards).
9. **`corrector.py`** — the interactive CLI wrapper around 8. Detail: `src/ziplex/AGENTS.md`.
10. **`packager.py`** (`save_aif()`) — splits `compressed` per file → `<name>.detail.json`, manifest → `<name>.cache.json`.

Beyond the numbered pipeline, `src/ziplex/AGENTS.md` also covers: `text_references.py`/`go_packages.py` (relationship enrichment past code imports), `tech_stack.py`/`folder_summary.py` (free project-level fact blocks), `freshness.py`/`doctor.py`/`search.py`/`confidence.py` (staleness, environment checks, search, summary-trust scoring), `query_service.py` (the read-only query core behind MCP and the GUI), `config.py`/`settings.py` (per-project/per-user config), and `skill_export.py`/`mcp_server.py` (the two distribution channels beyond the GUI).

The final `aif.json` shape: `{ project: {name, prompt, tech_stack: [...], security_scan: {flagged, included_anyway, excluded}, scope: {include: [...], ignore: [...]}, format_notes, language}, rules: [...], folders: {folder-path: {summary, confidence, file_count}}, tokens: {...}, files: {name: {summary, confidence}}, relationships: {...} }`, with siblings `<name>.detail.json` shaped `{ file-name: {compressed}, ... }` and `<name>.cache.json` shaped `{ file-name: content-hash, ... }` (both flat, same file-name keys as `files` — not nested under a `files` key). `project.security_scan`/`project.format_notes`/`project.scope` are small free additions explained in `src/ziplex/AGENTS.md`'s closing paragraph, attached so `aif.json` is self-explanatory even outside any tool session.

Every LLM-facing function in `llm.py` follows the same contract: prompt says "JSON only, nothing else," caller does `json.loads()` and falls back to a default (`{}`, `[]`, `""`) on `JSONDecodeError` — callers in `packager.py` loop until they get a non-empty result or the user intervenes via `checkpoint.handle_llm_failure`.
