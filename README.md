# Ziplex

**English** | [한국어](README_ko.md)

**Turn any local project into a context file an AI can load instantly — instead of reading through hundreds of raw files.**

Ziplex walks a project, compresses and structures it with Tree-sitter, summarizes it with an LLM, and lets a human correct the result before it ships. The output is `aif.json`: a small, structured "AI context format" file.

> ⚠️ Under active development. Interfaces and output format may still change.

---

## How it works

```
project/  ──►  collect  ──►  security scan  ──►  select  ──►  parse & extract
                                                                     │
              aif.json  ◄──  human correction  ◄──  LLM summarize  ◄┘
             + detail.json
```

1. **Collect** — walk the project, skipping `node_modules/`, build caches (`.gradle/`, `target/`, `.pytest_cache/`, …), and anything the project's own `.gitignore` excludes. Any file that can't be decoded as text (images, binaries, compiled artifacts) is dropped too — no fixed ignore list can name every binary format, so this is checked directly rather than guessed from the filename.
2. **Security scan** — every remaining file is checked for secrets (API keys, passwords, tokens) via `secretlint`, with a regex-based fallback if it isn't installed. Flagged files never enter the pipeline.
3. **Select** — interactively choose which files to include, or skip straight to everything safe with `--auto`.
4. **Parse & extract** — Tree-sitter parses each supported source file to pull out function signatures, imports, and (for decorator-based routes) API endpoints.
5. **Compress** — function bodies are replaced with a single marker, keeping structure while cutting tokens. Non-code text (JSON, Markdown, plain text) gets its own compression pass — Markdown code blocks even reuse the code compressor by detected language.
6. **Summarize** — Gemini generates a one-line summary per file, plus project-wide coding rules inferred from the collected signatures and an AI-facing guide describing the project.
7. **Correct** — a human reviews and edits the project name, guide, rules, and every summary, then can manually reparent files in the dependency tree (with cycle detection) before the final relationship graph is built.
8. **Package** — the lean `aif.json` (summary + relationships) is written for immediate loading; the heavier compressed code goes into a sibling `detail.json`, kept on disk for on-demand use rather than shipped on every file by default.

## Features

- **Multi-language code compression** — Python, Java, TypeScript, JavaScript, Lua, GDScript, Go, C++, and Rust today, via a per-language config table (`LanguageConfig`) so adding a new grammar is a single entry, not a rewrite.
- **Text-aware compression beyond code** — dedicated compressors for JSON and Markdown (including embedded code fences) and plain text, using the same body-preserving philosophy as the code compressor.
- **Relationships past code files, for free** — a Godot scene's `[ext_resource path="res://player.gd"]`, a Markdown doc mentioning `player.gd`, a README documenting the project layout by filename: matched against the real collected-file list (no LLM call, no generic "looks like a path" guessing) so files with no Tree-sitter grammar stop showing up as disconnected leaves in `relationships` when they obviously reference other project files.
- **Security scanning built in** — `secretlint` first, regex fallback second; sensitive files never make it past collection.
- **Human-in-the-loop correction, opt-in** — every LLM output (summaries, rules, project guide, dependency tree) is reviewable and editable before anything is saved, or skippable entirely with `--auto-correct`. File selection (`--auto`) and correction (`--auto-correct`) are independent flags, so `pack` can run fully headless for CI or scripted use.
- **Review that scales with project size, not file count** — reviewing every summary by hand stops being realistic well before a project hits a few hundred files. Each summary gets a free, no-LLM-call confidence score (0.0-1.0, how much its wording actually overlaps with the file's real signatures) — only the ones that look questionable get prompted during correction; the rest are auto-kept and just listed. The score itself ships in `aif.json` too, so an agent reading it knows which summaries are worth double-checking with `get_detail` before trusting them.
- **Honest token accounting** — `tiktoken`-based before/after comparison across GPT-4o, GPT-3.5, and GPT-4 encodings, measured against what actually ships in `aif.json`, not just the raw compression ratio.
- **Lean output, detail on request** — `aif.json` stays small (summaries + relationships); the full compressed body per file lives in `detail.json`, fetched on demand by the MCP server's `get_detail` tool (see below) rather than shipped on every file up front.
- **Resilient to LLM flakiness** — retries with backoff on rate limits, and a checkpoint system that lets a failed run resume later instead of restarting from scratch.
- **Incremental re-pack** — a content-hash manifest (`<name>.cache.json`) lets a later `pack` on the same project reuse an unchanged file's summary instead of paying for another LLM call, so keeping a project's `aif.json` current stays cheap enough to actually do routinely. Detecting *whether* it's gone stale is also exposed standalone via `check_freshness`, without triggering a re-pack (see MCP server below).
- **Provider-agnostic LLM layer** — swapping Gemini for another model is implementing one `generate()` method and registering it, not touching the rest of the pipeline.
- **Not just for git repos** — works on any collection of local files with relationships across extensions: game mods, asset projects, whatever isn't a typical software repo.
- **Scope a pack with `include`/`ignore` glob patterns** — one-off via `--include`/`--ignore`, or persisted per-project in a `.ziplex.json` (`init` scaffolds one) so a large repo doesn't mean either clicking through every file or an all-or-nothing `--auto`.
- **Local GUI, no CLI required** — pack a project, review its summaries, and edit relationships all from a native window (or a plain browser tab) instead of the terminal; the same GUI doubles as a read-only browse/search companion over an already-packed project for anyone without Claude Code or MCP access (see [GUI](#gui) below).
- **Claude Agent Skill export** — turn an already-packed project into a `.claude/skills/` directory Claude Code discovers and progressively loads on its own, no MCP server required (see [Claude Agent Skill export](#claude-agent-skill-export) below).
- **Tech stack detection, for free** — `package.json`/`requirements.txt`/`pyproject.toml`/`Cargo.toml`/`go.mod`/`Gemfile`/`composer.json`/`pom.xml` at the project root are read directly (no LLM call) for declared dependencies, shipped as `aif.json`'s `project.tech_stack` — a direct fact alongside `rules`' LLM-inferred conventions, not a replacement for them.
- **CI token-budget guard** — `pack --max-tokens N` fails with a non-zero exit code if the packed payload exceeds `N` tokens for a chosen model, so a context budget regression fails a build instead of shipping silently.
- **Structural-only mode, no API key needed** — `pack --no-llm` skips every LLM call entirely (no `GEMINI_API_KEY`, no network): each file's summary becomes a deterministic listing of its own extracted signatures/dependencies instead of an LLM-written description, and `rules`/the AI guide are skipped rather than faked. Every Tree-sitter/regex-based step (extraction, compression, the dependency graph, tech stack detection) still runs exactly as normal.

## Quick start

```bash
venv\Scripts\activate
pip install -r requirement.txt        # note: filename has no "s"
```

Add a `.env` with `GEMINI_API_KEY=...` (optionally `GEMINI_MODEL=...` too, if `gemini-flash-latest` is having a rough day — see [Tech stack](#tech-stack)), then:

```bash
python src/cli.py pack ./your-project/                        # full pipeline, interactive
python src/cli.py pack ./your-project/ --auto --auto-correct  # fully non-interactive (CI, scripted runs)
```

`--auto` (skip interactive file selection) and `--auto-correct` (skip interactive correction) are independent, so any combination of the two works. Re-running `pack` on a project you've packed before only re-summarizes files that actually changed (by content hash) — everything else reuses its previous summary instead of another LLM call.

<details>
<summary>Every <code>pack</code> flag</summary>

| Flag | Effect |
|---|---|
| `--auto` | Skip interactive file selection, include everything safe |
| `--auto-correct` | Skip interactive correction, auto-accept whatever the LLM produced |
| `-o, --output <path>` | Custom output path (writes `<path>` + sibling `.detail.json`/`.cache.json`) |
| `--no-cache` | Force-resummarize every file, ignoring any previous pack found on disk |
| `--include <patterns>` | Only pack files matching these comma-separated globs, on top of `.ziplex.json` (below) |
| `--ignore <patterns>` | Additionally exclude these comma-separated globs, on top of `.ziplex.json` |
| `--max-tokens N` (+ `--max-tokens-model M`) | CI guard: exit code 1 if the packed payload exceeds `N` tokens for model `M` (default GPT-4o) |
| `--no-llm` | No `GEMINI_API_KEY`/network at all — structural summaries only, `rules`/AI guide skipped |

</details>

### Config file

`python src/cli.py init ./your-project/` scaffolds `.ziplex.json` in the target project (not in Ziplex's own repo) so `include`/`ignore` glob patterns don't need retyping on every `pack`:

```jsonc
// your-project/.ziplex.json
{
  "include": ["src/**/*.py", "*.md"],  // empty = everything not ignored (default)
  "ignore": ["**/*.generated.*"]        // extra patterns beyond DEFAULT_IGNORE/.gitignore
}
```

`--include`/`--ignore` CLI flags add to this file's patterns rather than replacing them. Every subcommand that previews what `pack` would collect (`collect`, `tree`, `tokens`, `search`, `freshness`, `select`, `analyze`) reads the same file, so none of them drift out of sync with what a real `pack` on that project would actually see. Worth committing alongside the project the same way `aif.json`/`detail.json` already can (see Team use below) — it documents how that project gets packed.

<details>
<summary>Every command</summary>

| Command | Description |
|---|---|
| `pack <path>` | Full pipeline — the one most people want |
| `init <path>` | Scaffold `.ziplex.json` (`include`/`ignore` glob patterns) in the target project |
| `collect <path>` | File collection + security scan only |
| `tokens <path>` | Token count, before/after compression |
| `tree <path>` | Dependency tree only |
| `search <path> <pattern>` | Regex search across all safe files (`--context N`, `--ignore-case`) |
| `detail <name>.detail.json <file-key>` | Partial read of one file's compressed body (`--start`/`--end`) |
| `freshness <path> <name>.cache.json` | Hash-check `aif.json` against the files on disk — no LLM calls |
| `skill <name>.json` | Export as a Claude Agent Skill (`.claude/skills/<slug>/`) — no MCP server needed |
| `select <path>` | Interactive file selection only |
| `analyze <path>` | LLM analysis only |
| `signatures \| dependencies \| api \| compress \| debug <file>` | Run one extraction step on a single file |

</details>

## Testing

```bash
pip install -r requirement-dev.txt   # adds pytest on top of requirement.txt
pytest
```

Covers the deterministic core — compressors, the Tree-sitter extractor, the collector's ignore/binary-file filtering, the dependency-graph operations (`build_tree`/`has_cycle`/`move_file`), and the pure `aif`-editing API — plus a full `pack()` run against a network-free `MockProvider` instead of Gemini, exercising checkpointing, parallel summaries, and token counting end to end without the cost or latency of a real LLM call.

Want to smoke-test `pack` against a real project without waiting on Gemini? `LLM_PROVIDER=mock python src/cli.py pack <project> --auto --auto-correct` runs the whole pipeline network-free in under a second.

## Output format

```jsonc
// aif.json — small, loaded up front
{
  "project": {
    "name": "...", "prompt": "...",
    "tech_stack": [{ "manifest": "package.json", "language": "JavaScript/TypeScript", "package_manager": "npm", "dependencies": ["react", "..."], "dependencies_truncated": false }]
  },
  "rules": ["..."],
  "tokens": { "GPT-4o": { "original": 3100, "compressed": 749, "saved_pct": 75.8 } },
  "files": { "src/App.tsx": { "summary": "...", "confidence": 0.83 } },
  "relationships": { "src/App.tsx": { "internal": ["..."], "external": ["react"] } }
}
```

```jsonc
// out.detail.json — heavier, fetched only when a file actually needs a closer look
{
  "src/App.tsx": { "compressed": "import React ...\n    ⋮----\nexport default App" }
}
```

```jsonc
// out.cache.json — internal bookkeeping, not meant for an AI to read; a
// content-hash snapshot of what was packed, for check_freshness to diff
// against later
{
  "src/App.tsx": "3b1c2e...(sha256)"
}
```

## MCP server

Query an already-packed project directly from Claude Code, Cursor, or any other MCP client — no copy-pasting `aif.json` into a prompt.

```bash
python src/mcp_server.py                              # run directly (stdio transport)
claude mcp add ziplex -- python src/mcp_server.py      # register with Claude Code, from the repo root
```

| Tool | What it does |
|---|---|
| `get_overview(aif_path, project_path?)` | Project guide, coding rules, token stats — call this first |
| `list_files(aif_path, project_path?)` | Every file mapped to its summary + confidence score |
| `get_relationships(aif_path)` | The whole dependency graph at once — every file's internal/external edges |
| `get_dependents(aif_path, file)` | Files that directly depend on `file` |
| `get_blast_radius(aif_path, file)` | Every file transitively affected by a change to `file` |
| `get_detail(aif_path, file, start_line?, end_line?)` | A file's compressed source, in full or by line range |
| `check_freshness(project_path, aif_path)` | Hash-check the pack against the files on disk — no LLM calls |
| `search_project(project_path, pattern, ...)` | Regex search across the project's original files |

Read-only and deliberately so: every tool serves an `aif.json`/`detail.json` a human already reviewed via `correct_aif()` — none of them re-pack or re-correct a project on their own, since that would skip the human-in-the-loop step that's the point of Ziplex. `get_dependents`/`get_blast_radius` run on the same human-corrected `relationships` graph `pack` builds — not a fresh, uncorrected guess.

`aif.json`/`detail.json` are snapshots from the last `pack` run, so they can drift from an actively-changing project. Every tool above except `search_project` (which always reads files live) trusts that snapshot — call `check_freshness` first if you suspect it's gone stale, or just pass `project_path` to `get_overview`/`list_files` and let them check for you: a stale pack gets an extra `_stale` field in the result instead of silently being trusted. It won't fix anything, but it tells you whether a re-`pack` is warranted before you trust the rest.

## GUI

A local, single-user GUI for two situations: packing a project without touching a terminal, and browsing an already-packed project somewhere Claude Code (or MCP generally) isn't available but a browser-based AI chat is.

```bash
python src/gui/gui_server.py                                            # native window (pywebview)
python src/gui/gui_server.py --aif out.json --project ./your-project/   # prefill the landing page
python src/gui/gui_server.py --no-window                                # plain browser tab instead
```

**Pack from the GUI** — pick a project folder with the native folder picker, check off which files to include (the same safe/dangerous split `collect`'s security scan produces), optionally check "no LLM" (`pack --no-llm`'s GUI equivalent), and watch the pack run in the background. Analysis pauses for review before anything is saved: edit the project name, guide, rules, and per-file summaries (only the low-confidence ones are flagged, same triage the CLI uses). The dependency graph opens as a collapsible whole-tree overview first — click a file's name once you've spotted one worth fixing to drop into an edit view for just that file, linking or unlinking individual edges (a file with more than one real parent keeps its other references intact).

**Browse an existing pack** — the same overview/files/relationships/detail/search views the MCP server exposes, as web pages instead of MCP tool calls. The Relationships page reuses the same whole-tree-then-edit-one-file flow packing uses, so a relationship noticed after the fact can be fixed without re-running the pipeline. Each page has a Copy button; the intended flow is looking around here and pasting what's useful into a separate AI chat by hand, not the GUI talking to that chat itself.

Binds to `127.0.0.1` only — no `--host` flag, no way to expose it to a network.

## Claude Agent Skill export

A third way to reach a packed project, alongside the MCP server and the GUI — for when Claude Code is available but registering an MCP server isn't (or is more setup than the moment calls for):

```bash
python src/cli.py skill result/my-project.json               # writes .claude/skills/my-project/
python src/cli.py skill result/my-project.json -o some/dir    # custom output directory
```

Generates a [Claude Agent Skill](https://code.claude.com/docs/en/skills) — `SKILL.md` plus `references/overview.md`/`files.md`/`relationships.md`/`detail.json` — that Claude Code discovers and progressively loads on its own once it sits under `.claude/skills/`, no server process required. Unlike [repomix](https://repomix.com/guide/agent-skills-generation)'s equivalent `--skill-generate` feature, `references/files.md` never embeds full raw source — it stays to summaries and confidence scores, exactly what `aif.json` itself already restricts to; the *compressed* body only ships as `references/detail.json`. Committing the generated directory works the same way committing `aif.json`/`detail.json` does (see Team use below) — it's just files.

## Team use

Ziplex packs one person's local snapshot — there's no shared server or live sync. That doesn't rule out team use, though: `aif.json`/`detail.json`/`cache.json` are just files, so a team can commit them to the project's own repo the same way any other generated-but-versioned artifact works.

- Whoever runs `pack` after a meaningful change commits the refreshed output alongside their code change.
- Everyone else's copy is only as current as the last commit that touched it — `check_freshness` (or `get_overview`/`list_files` with `project_path` passed, see above) tells them whether their working copy has drifted from what's committed, without re-`pack`ing just to find out.
- This is a convention, not a feature Ziplex enforces or coordinates: no merge/conflict resolution, and no defined "who wins" if two people repack independently before either commits.

## Tech stack

Python 3.11 · [Tree-sitter](https://tree-sitter.github.io/tree-sitter/) (Python/Java/TypeScript/JavaScript/Lua/GDScript/Go/C++/Rust grammars, GDScript via `tree-sitter-language-pack`) · [tiktoken](https://github.com/openai/tiktoken) · Gemini API (`gemini-flash-latest` by default, overridable via `GEMINI_MODEL`; plain REST via `requests`) · [MCP](https://modelcontextprotocol.io/) · Flask · pywebview · `secretlint` · `pathspec`

## Roadmap

**Selective file delivery to AI** — pick specific files in Ziplex and send them straight into a chat with full context attached (dependencies, signature, summary) — no copy-pasting. *(The read-only "browse and copy by hand" half of this ships via the [GUI](#gui); [Skill export](#claude-agent-skill-export) removes the copy-paste step entirely for Claude Code specifically, though it's a static snapshot, not a live per-file pick. What's still open: the same no-copy-paste experience for a plain web chat with no local agent at all.)*

**Relationship analysis across all file types** — extend the dependency graph past code files. The free, syntactic half shipped (see [Relationships past code files, for free](#features) above -- matching literal path/filename mentions in non-code text, no LLM call). What's still open: true *semantic* connections no literal string match could find (a handler that implements an API a doc merely describes in prose) via LLM inference over already-generated summaries -- lower-confidence, review-gated the way low-confidence summaries already are, and deliberately not started until that confidence/review design gets its own pass.

**Expanded language support** — broader Tree-sitter coverage for game-specific languages and additional frameworks. Lua, GDScript, Go, C++, and Rust shipped (GDScript's grammar has no dedicated PyPI package of its own -- sourced from `tree-sitter-language-pack`'s bundled copy instead). ZenScript (a niche Minecraft modding DSL) is still open; it doesn't appear to have a maintained Tree-sitter grammar at all as of this check. C#/PHP/Ruby remain candidates from the same shortlist Go/C++/Rust were picked off of, all confirmed to have dedicated, well-maintained PyPI `tree-sitter-*` packages.

## License

[MIT](LICENSE)
