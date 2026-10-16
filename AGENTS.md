# AGENTS.md

Guide for AI coding agents in this repository. Every `CLAUDE.md` here is a one-line `@AGENTS.md` import — edit AGENTS.md, never CLAUDE.md ([`docs/team/agents-md.md`](docs/team/agents-md.md)). This is a current-state map; the history behind decisions is in `docs/HISTORY.md`, the evidence in `docs/EXPERIMENTS.md` and `docs/REVIEW-2026-10-14.md` (none auto-loaded — open them when the "why" matters).

Nested docs, auto-loaded in their subtree:
- `src/ziplex/core/AGENTS.md` — the graph engine: index, query API, resolvers (Python/TS/Go), text references, test mapping. No LLM/UI imports (layering test).
- `src/ziplex/AGENTS.md` — `pack()`/`save_aif()`, the optional LLM layer (`enrich/`), exports, aif.json query layer, MCP server, hook, CLI, config.
- `src/ziplex/file/AGENTS.md` — collection, secret scanning, `relationship.py` (stem/relative resolution, `build_tree`, graph algorithms).
- `src/ziplex/extract/code/AGENTS.md` / `src/ziplex/extract/text/AGENTS.md` — Tree-sitter extraction per language / text compressors.
- `src/ziplex/gui/AGENTS.md` — the read-only viewer.

## What this is

Two products on one parser/resolver:
- **The graph (the lead)** — a zero-config, incremental dependency-graph index (`<project>/.ziplex/index.db`) that answers "which tests does this change affect / what depends on X / how does A reach B": CLI `index`/`deps`/`impact`/`path`, MCP v2 (`ziplex-mcp`), the editor hook (`ziplex-hook`), the CI template `docs/ci/impacted-tests.yml`. No LLM, no network.
- **The pack** — project → `aif.json` (per-file summaries, rules, AI guide, folder map, relationships) + `<name>.detail.json` + `<name>.cache.json`. Summaries are structural, or LLM-written with the `[llm]` extra. Served by the legacy MCP (`ziplex-mcp --aif`), skill/flat export and the read-only GUI (`[gui]` extra).

Packaging: src-layout (`src/ziplex/` is the `ziplex` package), package-relative imports only. Console scripts: `ziplex`, `ziplex-mcp`, `ziplex-hook`, `ziplex-gui`. Core dependencies cover the graph; `pip install "ziplex[llm]"` adds requests/python-dotenv/tiktoken, `"ziplex[gui]"` adds flask/pywebview/watchdog, `[all]` both, `[dev]` everything + pytest.

## Commands

Setup (Windows, matches `venv/`): `venv\Scripts\activate` then `pip install -e ".[dev]"`. Without installing, run subcommands as `PYTHONPATH=src python -m ziplex.cli ...`. A `.env` with `GEMINI_API_KEY=...` (or `ziplex settings set ...`) is only needed for LLM packs.

Graph (project defaults to cwd; an unknown file or git error → stderr + exit 2):
```
ziplex index [path] [--check] [--json]          # refresh the index; --check exits 1 if anything differed
ziplex deps <file...> [--in [--depth N]] [--mentions] [--external] [--json]
ziplex impact [file...] [--diff worktree|staged|REF] [--format text|json|paths|commands]
ziplex path <a> <b>                              # shortest import chain; exit 1 when none
ziplex tree <path>                               # dependency tree
ziplex install claude [--hook] [--dry-run]       # register ziplex-mcp (+ PostToolUse hook in .claude/settings.json)
ziplex-mcp [--project PATH]                      # MCP v2, stdio; --aif <name>.json or --legacy-tools for the old aif.json tools
ziplex-hook                                      # PostToolUse hook: stdin JSON in, additionalContext out, always exit 0
```
`impact` always exits 0. When a targeted run is unsafe (`run_all`: build/config/lockfile changed, or a file was deleted) `--format paths` prints nothing so `pytest $(ziplex impact --format paths)` runs everything; `--format commands` prints whole-suite commands. Tests are split into *certain* (import-time) and *possible* (only via a function-local import): text output and the hook run the certain ones, `paths`/`commands`/`json` include all.

Pack and exports:
```
ziplex pack <path> [--no-llm] [--no-cache] [-o out.json] [--include "a,b"] [--ignore "c"] [--lang en|ko] [--max-tokens N [--max-tokens-model M]]
ziplex freshness <path> <name>.cache.json        # exit 1 if a saved pack drifted from disk (CI gate: docs/ci/freshness-gate.yml)
ziplex skill <name>.json [--project <path>]      # Claude Agent Skill export
ziplex flatten <name>.json                       # one Markdown file
ziplex init <path> | settings [set <k> <v>] | doctor [path] | --version
ziplex dev <collect|tokens|search|detail|signatures|dependencies|api|compress|debug> ...   # debug tools
```
`pack` is always non-interactive (`--auto`/`--auto-correct` are accepted, hidden no-ops), skips work when the previous pack is still current, and reuses summaries by content hash. Network-free smoke test: `LLM_PROVIDER=mock ziplex pack <path>`.

GUI (read-only viewer of a saved pack): `ziplex-gui [--aif out.json --project ./p] [--no-window]`.

Tests: `pytest` (`pytest.ini` puts `src/` on the path). Nothing calls a real LLM or the network. `tests/` mirrors the package: `tests/core/`, `tests/enrich/`, `tests/extract/{code,text}/`, `tests/file/`, `tests/gui/`; root-level modules' tests sit flat in `tests/`. Graph regression nets: `tests/test_graph_golden.py`, `tests/core/test_index.py` (incremental == fresh build), `tests/core/test_layering.py`. Measurement scripts (not tests): `eval/` — see `eval/README.md`.

## Shapes

aif.json: `{project: {name, prompt, tech_stack, security_scan: {flagged, included_anyway, excluded}, scope: {include, ignore}, format_notes, language, ziplex_version, llm}, rules, folders: {path: {summary, file_count}} (multi-file folders), tokens, files: {name: {summary}}, relationships: {file: {internal, external}} (certain edges only)}`; detail.json `{file: {compressed, text_refs?}}` (text_refs = prose mentions); cache.json `{file: content-hash}`. Readers of a saved graph go through `aif_io.attach_weak_edges()`; writers through `aif_io.write_aif()`.
