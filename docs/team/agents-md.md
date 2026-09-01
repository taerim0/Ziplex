# Why this repo has both AGENTS.md and CLAUDE.md

Project instructions live in `AGENTS.md` (root + one per subdirectory that
has one) — the emerging cross-tool convention several AI coding agents
(OpenAI Codex CLI, Cursor, Devin, Windsurf, and others) read directly.
Claude Code specifically does **not** auto-load `AGENTS.md` on its own
(confirmed against Claude Code's own docs) — it only reads `CLAUDE.md`.

So each directory that carries an `AGENTS.md` also carries a `CLAUDE.md`
that's just:

```markdown
@AGENTS.md
```

Claude Code's `@import` syntax loads the imported file's content at session
start as if it were written inline — this is a zero-duplication bridge, not
a copy that can drift out of sync. Editing `AGENTS.md` is enough; never
hand-edit a `CLAUDE.md` stub to add content of its own unless it's something
genuinely Claude-Code-specific (in which case, append it *after* the
`@AGENTS.md` line, per Claude Code's own documented pattern for this).

**On Windows** (this repo's primary dev environment): a symlink
(`ln -s AGENTS.md CLAUDE.md`) is the alternative some projects use instead,
but it needs Administrator privileges or Developer Mode enabled to create on
Windows — the `@AGENTS.md` import avoids that entirely and works
identically cross-platform, which is why this repo uses the import instead
of a symlink.

**Adding a new directory-scoped doc**: write `AGENTS.md` for that
directory, then add a matching one-line `CLAUDE.md` next to it, then add it
to the root `AGENTS.md`'s own directory-scoped-detail list.

**If you use a tool other than Claude Code**: check that tool's own docs for
whether it auto-loads a nested `AGENTS.md` the way Claude Code auto-loads a
nested `CLAUDE.md` — this hasn't been verified against any tool other than
Claude Code itself.
