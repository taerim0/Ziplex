# Ziplex

**English** | [한국어](README_ko.md)

**Turn any local project into a context file an AI can load instantly — instead of reading through hundreds of raw files.**

Ziplex walks a project, compresses and structures it with Tree-sitter, summarizes it with an LLM (optional — see [Quick start](#quick-start) for the no-API-key path), and lets a human correct the result before it ships. The output is `aif.json`: a small, structured "AI context format" file.

> ⚠️ Under active development. Interfaces and output format may still change.

---

## Where it helps

- **Giving an AI assistant your project's shape once, not every session** — paste `aif.json` into a chat, point an MCP client at it, or open it in the [GUI](#gui), instead of re-explaining file layout and conventions every time.
- **Multi-language projects** — a TypeScript frontend, a Python or Java backend, config and docs in between: one pack covers all of it, with cross-file relationships intact.
- **Game modding and asset projects that aren't git repos** — a Godot scene referencing a GDScript/Lua script by path, a Lua mod with loose asset files: Ziplex tracks those relationships even for files with no Tree-sitter grammar of their own (see [Features](#features)).
- **Onboarding an AI onto a codebase you didn't write** — summaries are human-reviewed before they ship, so you're not trusting a raw LLM guess about unfamiliar code.
- **Keeping a CI context budget honest** — `--max-tokens` fails a build if the packed payload grows past what a target model can hold.
- **Sharing one AI context across a team** — `aif.json`/`detail.json` are just files; commit them like any other generated artifact (see [Team use](#team-use)).

## Quick start

```bash
pip install ziplex        # or: venv\Scripts\activate && pip install -e . from a clone
```

**Try it right now — no API key, no signup, no network call:**

```bash
ziplex pack ./your-project/ --auto --no-llm
```

This runs the full pipeline (Tree-sitter compression, dependency graph, tech-stack detection) and writes a real `aif.json` — summaries are just a structural listing of each file's own signatures instead of an LLM-written description. See what that looks like on your own project before deciding whether the AI-written version is worth an API key.

Liked it? Add a `.env` with `GEMINI_API_KEY=...` (optionally `GEMINI_MODEL=...` too — see [Tech stack](#tech-stack)), drop `--no-llm`, and get real AI-written summaries plus inferred coding rules and a project guide:

```bash
ziplex pack ./your-project/                        # full pipeline, interactive
ziplex pack ./your-project/ --auto --auto-correct  # fully non-interactive (CI, scripted runs)
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
| `--lang en\|ko` | Language every summary/rule/AI guide is *written in* (default/recommended `en`) — independent of what language your code or comments are in |

</details>

<details>
<summary>Other AI providers (OpenAI-compatible, Ollama, LM Studio, Claude)</summary>

Gemini is the default, but every LLM-calling step (summaries, coding rules, the project guide) can run through a different provider instead — set `LLM_PROVIDER` (env var, for CLI/CI use) or pick one on the GUI's [Options page](#gui) (takes effect on the very next pack, no restart needed):

| Provider | `LLM_PROVIDER` | Config |
|---|---|---|
| Gemini (default) | `gemini` | `GEMINI_API_KEY`, optional `GEMINI_MODEL` |
| OpenAI-compatible — real OpenAI, Ollama, LM Studio, vLLM, OpenRouter, Groq, llama.cpp's server, ... | `openai` | `OPENAI_API_KEY` (often unneeded for a local server), `OPENAI_BASE_URL`, `OPENAI_MODEL` |
| Claude (Anthropic) | `claude` | `ANTHROPIC_API_KEY` (or `CLAUDE_API_KEY`), optional `CLAUDE_MODEL` |

A local model (Gemma, Llama, Mistral, ...) served through Ollama or LM Studio is just the `openai` provider pointed at that server — `OPENAI_BASE_URL=http://localhost:11434/v1` (Ollama) or `http://localhost:1234/v1` (LM Studio), `OPENAI_MODEL` set to whatever name that server expects, no API key needed either way. The GUI's Options page has one-click presets for both.

</details>

### Config file

`ziplex init ./your-project/` scaffolds `.ziplex.json` in the target project (not in Ziplex's own repo) so `include`/`ignore` glob patterns don't need retyping on every `pack`:

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

## How it works

Ziplex collects a project's files (skipping build artifacts and anything `.gitignore`d), security-scans them for secrets, and parses the safe ones with Tree-sitter to pull out signatures, imports, and API routes. Function bodies get compressed down to a single marker; an LLM then writes a one-line summary per file plus project-wide coding rules and an AI-facing guide. A human reviews and edits all of that — only low-confidence summaries get flagged, not every file — before the final `aif.json` (small, loaded up front) and its heavier sibling `detail.json` (full compressed body, fetched on demand) are saved.

## Output format

A pack produces three files, each read differently:

| File | Contains | Read when |
|---|---|---|
| `aif.json` | Project guide, coding rules, token stats, one summary + confidence score per file, the full dependency graph | Every time — small enough to load up front |
| `<name>.detail.json` | The full compressed source, per file | On demand, when a summary alone isn't enough (`get_detail`, the GUI's detail view) |
| `<name>.cache.json` | A content-hash of every packed file | Never by a human — internal bookkeeping for `check_freshness` and incremental re-pack |

```jsonc
// aif.json — small, loaded up front
{
  "project": {
    "name": "...", "prompt": "...",
    "tech_stack": [{ "manifest": "package.json", "language": "JavaScript/TypeScript", "package_manager": "npm", "dependencies": ["react", "..."], "dependencies_truncated": false }],
    "security_scan": { "flagged": 0, "included_anyway": 0, "excluded": 0 },
    "format_notes": "..."  // fixed legend explaining confidence/⋮---- to a reader with no other Ziplex context
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

## Features

- **Multi-language, structure-aware compression** — Python, Java, TypeScript, JavaScript, Lua, GDScript, Go, C++, Rust, C#, PHP, Ruby via Tree-sitter, plus dedicated JSON/YAML/Markdown/plain-text compressors, all preserving structure while cutting tokens. Works on any collection of local files with cross-file relationships, not just git repos — game mods and asset projects included.
- **Security scanning built in** — every file is checked for secrets (`secretlint`, regex fallback) before it ever enters the pipeline.
- **Human-in-the-loop, but it scales** — every LLM output (summaries, rules, project guide, dependency tree) is reviewable and editable, or skippable entirely with `--auto-correct`. Only low-confidence summaries get flagged for review — review time doesn't grow with project size.
- **Three ways to consume the result** — an [MCP server](#mcp-server) for Claude Code/Cursor/etc., a local [GUI](#gui) for packing and browsing without a terminal, or a [Claude Agent Skill export](#claude-agent-skill-export) that needs no server at all.
- **Cheap to keep current** — incremental re-pack only re-summarizes files that actually changed (content hash), retries with backoff on LLM flakiness, and checkpoints a failed run instead of restarting from scratch.
- **Bring your own LLM** — Gemini by default, or switch to any OpenAI-compatible endpoint (real OpenAI, Ollama, LM Studio, vLLM, OpenRouter, ...) or Claude directly, via `LLM_PROVIDER` or the GUI's Options page — including a fully local setup with no API key or network call at all (see [Other AI providers](#quick-start)).
- **No API key required, if you want** — `pack --no-llm` skips every LLM call and still produces structural summaries, dependency graphs, and tech-stack detection. Scope any pack with `.ziplex.json`/`--include`/`--ignore`, and guard CI budgets with `--max-tokens`.

## Testing

```bash
pip install -e ".[dev]"   # adds pytest on top of the base install
pytest
```

Covers the deterministic core — compressors, the Tree-sitter extractor, the collector's ignore/binary-file filtering, the dependency-graph operations (`build_tree`/`has_cycle`/`move_file`), and the pure `aif`-editing API — plus a full `pack()` run against a network-free `MockProvider` instead of Gemini, exercising checkpointing, parallel summaries, and token counting end to end without the cost or latency of a real LLM call.

Want to smoke-test `pack` against a real project without waiting on Gemini? `LLM_PROVIDER=mock ziplex pack <project> --auto --auto-correct` runs the whole pipeline network-free in under a second.

## MCP server

Query an already-packed project directly from Claude Code, Cursor, or any other MCP client — no copy-pasting `aif.json` into a prompt.

```bash
ziplex-mcp                              # run directly (stdio transport)
claude mcp add ziplex -- ziplex-mcp     # register with Claude Code
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
ziplex-gui                                            # native window (pywebview)
ziplex-gui --aif out.json --project ./your-project/   # prefill the landing page
ziplex-gui --no-window                                # plain browser tab instead
```

**Pack from the GUI** — pick a project folder with the native folder picker, check off which files to include (the same safe/dangerous split `collect`'s security scan produces), pick the packing language (`pack --lang`'s GUI equivalent — what language summaries/rules/the AI guide are *written in*, separate from the Options page's own display-language setting below), optionally check "no LLM" (`pack --no-llm`'s GUI equivalent), and watch the pack run in the background. Analysis pauses for review before anything is saved: edit the project name, guide, rules, and per-file summaries (only the low-confidence ones are flagged, same triage the CLI uses). The dependency graph opens as a collapsible whole-tree overview first — click a file's name once you've spotted one worth fixing to drop into an edit view for just that file, linking or unlinking individual edges (a file with more than one real parent keeps its other references intact).

**Browse an existing pack** — the same overview/files/relationships/detail/search views the MCP server exposes, as web pages instead of MCP tool calls. The Relationships page reuses the same whole-tree-then-edit-one-file flow packing uses, so a relationship noticed after the fact can be fixed without re-running the pipeline. Each page has a Copy button; the intended flow is looking around here and pasting what's useful into a separate AI chat by hand, not the GUI talking to that chat itself.

**Options page** — display language, the default output folder new packs save to, and which AI provider every pack uses (Gemini / OpenAI-compatible / Claude — see [Other AI providers](#quick-start)), with one-click presets for Ollama's and LM Studio's default local ports so a fully local setup needs no typed URL.

Binds to `127.0.0.1` only — no `--host` flag, no way to expose it to a network.

## Claude Agent Skill export

A third way to reach a packed project, alongside the MCP server and the GUI — for when Claude Code is available but registering an MCP server isn't (or is more setup than the moment calls for):

```bash
ziplex skill result/my-project.json               # writes .claude/skills/my-project/
ziplex skill result/my-project.json -o some/dir    # custom output directory
```

Generates a [Claude Agent Skill](https://code.claude.com/docs/en/skills) — `SKILL.md` plus `references/overview.md`/`files.md`/`relationships.md`/`detail.json` — that Claude Code discovers and progressively loads on its own once it sits under `.claude/skills/`, no server process required. Unlike [repomix](https://repomix.com/guide/agent-skills-generation)'s equivalent `--skill-generate` feature, `references/files.md` never embeds full raw source — it stays to summaries and confidence scores, exactly what `aif.json` itself already restricts to; the *compressed* body only ships as `references/detail.json`. Committing the generated directory works the same way committing `aif.json`/`detail.json` does (see Team use below) — it's just files.

## Team use

Ziplex packs one person's local snapshot — there's no shared server or live sync. That doesn't rule out team use, though: `aif.json`/`detail.json`/`cache.json` are just files, so a team can commit them to the project's own repo the same way any other generated-but-versioned artifact works.

- Whoever runs `pack` after a meaningful change commits the refreshed output alongside their code change.
- Everyone else's copy is only as current as the last commit that touched it — `check_freshness` (or `get_overview`/`list_files` with `project_path` passed, see above) tells them whether their working copy has drifted from what's committed, without re-`pack`ing just to find out.
- This is a convention, not a feature Ziplex enforces or coordinates: no merge/conflict resolution, and no defined "who wins" if two people repack independently before either commits. If it does conflict, don't hand-resolve the JSON — see [merge-conflicts.md](docs/team/merge-conflicts.md) for the recommended fix (clear the conflict, re-`pack`, review); [`gitattributes`](docs/team/gitattributes) keeps these files collapsed in GitHub's PR diff view.

**CI gate (optional):** `ziplex freshness <project> <name>.cache.json` exits non-zero when the committed output has drifted — no LLM calls, no `GEMINI_API_KEY`, just a hash comparison. Wire it into a PR check ([template](docs/ci/freshness-gate.yml)) to catch a stale, un-re-reviewed `aif.json` before it merges, without automating `pack` itself (which costs real LLM calls and skips human review — left as a manual step on purpose).

## Tech stack

Python 3.10+ · [Tree-sitter](https://tree-sitter.github.io/tree-sitter/) (Python/Java/TypeScript/JavaScript/Lua/GDScript/Go/C++/Rust/C#/PHP/Ruby grammars, GDScript via `tree-sitter-language-pack`) · [tiktoken](https://github.com/openai/tiktoken) · [ruamel.yaml](https://yaml.readthedocs.io/) · plain REST via `requests` against Gemini (`gemini-flash-latest` by default, overridable via `GEMINI_MODEL`), any OpenAI-compatible endpoint (real OpenAI, Ollama, LM Studio, vLLM, OpenRouter, ...), or Claude — see [Other AI providers](#quick-start) · [MCP](https://modelcontextprotocol.io/) · Flask · pywebview · `secretlint` · `pathspec`

## License

[MIT](LICENSE)
