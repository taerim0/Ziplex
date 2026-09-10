"""Exports an already-packed, already-corrected aif.json (+ its sibling
detail.json) as a Claude Agent Skill directory -- a third distribution
channel alongside the MCP server and the GUI, for Claude Code users who
haven't (or can't) register the MCP server: dropping `.claude/skills/<slug>/`
into a repo is enough for Claude Code to progressively load it on its own,
no server process required.

Inspired by repomix's --skill-generate feature (see the `ziplex-roadmap`
memory), but deliberately narrower in one respect: repomix's generated
files.md embeds every file's full raw source ("grep-friendly"); this module
never does that -- references/files.md stays to summaries + confidence,
exactly what aif.json itself already restricts to, and the compressed (not
raw) per-file body only ships as references/detail.json, copied verbatim
from the pack's own sibling file. Shipping full source in an
always-committed skill directory would undo the entire reason Ziplex
compresses in the first place.

Pure generation (generate_skill_files) is separate from the I/O wrapper
(export_skill) the same way edits.py/packager.py split -- generate_skill_files
takes already-loaded aif/detail dicts and returns {relative_path: content},
so it's directly testable without touching a filesystem.
"""
import json
import re
from pathlib import Path

from .query_service import _detail_path
from .confidence import project_confidence_summary


def _yaml_double_quoted(s: str) -> str:
    """Escapes s for embedding inside a YAML double-quoted scalar (SKILL.md's
    frontmatter `description` field). A project renamed (corrector.py or the
    GUI's set_project_name, neither of which validates the new name) to
    include a literal double-quote or newline would otherwise truncate or
    corrupt the YAML, breaking Claude Code's ability to load the generated
    skill at all -- backslash has to be escaped first, or escaping the other
    characters would introduce backslashes that then look like part of the
    original text instead of an escape sequence.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def _slugify(name: str) -> str:
    """Lowercase, non-alnum runs collapsed to one hyphen, trimmed -- Claude
    Skill names are simple identifiers, unlike aif.json's project name
    (which can be anything a human typed during correction).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def resolve_skill_display_name(aif: dict) -> str:
    """The display name _skill_md() writes into its own H1 heading and
    frontmatter description -- project.name if set, else the same
    _slugify() fallback _skill_md() itself falls back to.

    Split out so cli.py's collision check can compute the exact same
    fallback _skill_md() uses when comparing against an existing skill's
    heading (read_existing_skill_project_name()) instead of re-deriving it
    independently -- a real code-review finding: cli.py's own copy used
    `project.get("name") or ""` (no slug fallback), so a project with an
    empty/missing name triggered a false-positive "different project"
    collision warning on every single re-export of that same project.
    """
    name = (aif.get("project") or {}).get("name")
    return name or _slugify(name or "")


def _skill_md(aif: dict, slug: str) -> str:
    project = aif.get("project", {})
    name = project.get("name") or slug
    file_count = len(aif.get("files", {}))
    rule_count = len(aif.get("rules", []))
    description = (
        f"Pre-summarized reference for the {name} codebase, packed by Ziplex -- "
        f"file summaries, confidence scores, dependency graph, and coding rules. "
        f"Use when asked about this project's structure, what a file does, or how "
        f"files depend on each other, before reading raw source."
    )
    guide = (project.get("prompt") or "").strip()

    return f"""---
name: {slug}
description: "{_yaml_double_quoted(description)}"
---

# {name} — Ziplex reference

{guide}

This is a Ziplex-packed **snapshot** ({file_count} files, {rule_count} coding rules inferred), not a live view of the project -- if the project may have changed since this was generated, treat it as a starting point, not ground truth.

## How to use this

1. **`references/overview.md`** — the AI guide above, coding rules, a per-folder map (each folder's role, file count, and confidence), an overall confidence rollup, and token stats (how much smaller this reference is than the raw source).
2. **`references/files.md`** — every file's one-line summary and a confidence score (0.0-1.0). A low score means the summary's wording didn't overlap much with the file's real signatures -- worth a closer look before trusting it, not a guarantee it's wrong.
3. **`references/relationships.md`** — the dependency graph: what each file imports, and (derivable from it) what would be affected by changing one.
4. **`references/detail.json`** — full compressed source per file (structure and signatures kept, function bodies elided), keyed by the same relative path used everywhere else here. Read a file's entry only once its summary/confidence/relationships say it's worth a closer look -- that's the entire point of Ziplex's compression; reading every entry defeats it.
"""


def _overview_md(aif: dict) -> str:
    project = aif.get("project", {})
    rules = aif.get("rules", [])
    tokens = aif.get("tokens", {})
    files = aif.get("files", {})

    # Real gap found dogfooding Ziplex on its own repo: judging "is this
    # pack worth trusting" from inside a skill (no MCP server, no GUI
    # badge) used to mean reading every one of files.md's 100+ confidence
    # numbers by hand. Same rollup query_service.get_overview() attaches,
    # shared via confidence.project_confidence_summary() so the two
    # distribution channels can't report a different number for the same
    # pack.
    confidence_summary = project_confidence_summary(files)

    lines = [
        "# Overview", "",
        f"**Project**: {project.get('name') or '(unnamed)'}",
        f"**Files**: {len(files)}",
        f"**Confidence**: {confidence_summary['average']:.2f} average, "
        f"{confidence_summary['needs_review_count']} file(s) flagged for review "
        f"(see references/files.md)",
        "",
        "## AI guide", "",
        (project.get("prompt") or "").strip() or "(none)", "",
        "## Coding rules", "",
    ]
    lines += [f"- {r}" for r in rules] if rules else ["(none inferred)"]

    # Per-folder orientation -- a real gap found the same way: a folder
    # with dozens of files (this project's own src/ziplex/, 49 of them)
    # got no map at all here, only files.md's flat, alphabetized table.
    # Older aif.json files packed before "folders" existed simply have no
    # such key, so this section is skipped rather than shown empty --
    # same backward-compat guard tech_stack/security_scan below already use.
    folders = aif.get("folders")
    if folders:
        lines += ["", "## Folders", ""]
        for path in sorted(folders):
            info = folders[path]
            summary = (info.get("summary") or "").replace("|", "\\|").replace("\n", " ")
            count = info.get("file_count")
            count_text = f"{count} file(s)" if count is not None else "file count unknown"
            lines.append(f"- `{path}` ({count_text}, confidence {info.get('confidence', 1.0):.2f}): {summary}")

    # Free (no LLM call), manifest-based fact block -- see tech_stack.py.
    # Older aif.json files packed before this field existed simply have no
    # "tech_stack" key, so this section is skipped rather than shown empty.
    tech_stack = project.get("tech_stack")
    if tech_stack:
        lines += ["", "## Tech stack", ""]
        for stack in tech_stack:
            deps = ", ".join(stack.get("dependencies", []))
            if stack.get("dependencies_truncated"):
                deps += ", ..."
            lines.append(
                f"- **{stack.get('language')}** ({stack.get('manifest')}, {stack.get('package_manager')})"
                + (f": {deps}" if deps else "")
            )

    lines += [
        "", "## Token stats", "",
        "This reference (summaries only) vs. the raw original source:", "",
        "| Model | Original | This reference | Saved |",
        "|---|---|---|---|",
    ]
    for model, data in tokens.items():
        original = data.get("original", 0)
        compressed = data.get("compressed", 0)
        saved_pct = data.get("saved_pct", 0)
        lines.append(f"| {model} | {original:,} | {compressed:,} | {saved_pct}% |")

    # Same backward-compat guard as tech_stack above -- an aif.json packed
    # before packager.py started attaching this field simply has no
    # "security_scan" key, so the section is skipped rather than rendered
    # with misleading zeros.
    security_scan = project.get("security_scan")
    if security_scan:
        lines += ["", "## Security scan", ""]
        if security_scan.get("flagged"):
            lines.append(
                f"{security_scan['flagged']} file(s) flagged as potentially sensitive during packing -- "
                f"{security_scan['included_anyway']} included anyway (human-reviewed), "
                f"{security_scan['excluded']} left out of this reference entirely."
            )
        else:
            lines.append("No files were flagged as potentially sensitive.")

    return "\n".join(lines) + "\n"


def _files_md(aif: dict) -> str:
    files = aif.get("files", {})
    lines = [
        "# Files", "",
        f"{len(files)} files, sorted by path.", "",
        "| File | Confidence | Summary |",
        "|---|---|---|",
    ]
    for name in sorted(files):
        data = files[name]
        # Escape what would otherwise break the table -- a summary is free-
        # form LLM text, not something this module controls the shape of.
        summary = (data.get("summary") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{name}` | {data.get('confidence', 1.0):.2f} | {summary} |")
    return "\n".join(lines) + "\n"


def _relationships_md(aif: dict) -> str:
    relationships = aif.get("relationships", {})
    lines = ["# Dependency graph", "", "Each file's own outgoing edges -- what it depends on."]
    for name in sorted(relationships):
        deps = relationships[name]
        internal = deps.get("internal", [])
        external = deps.get("external", [])
        # A target both imported and text-mentioned counts as the former
        # only (see file/relationship.py's build_tree() docstring), so this
        # set is exactly the internal edges with no real import behind them.
        text_refs = set(deps.get("internal_text_refs", []))
        lines += ["", f"## `{name}`"]
        if internal:
            lines += [
                f"- depends on: `{d}`" + (" (text reference, not an import)" if d in text_refs else "")
                for d in internal
            ]
        if external:
            lines += [f"- depends on (external): `{d}`" for d in external]
        if not internal and not external:
            lines.append("- (no dependencies)")
    return "\n".join(lines) + "\n"


def generate_skill_files(aif: dict, detail: dict) -> dict[str, str]:
    """Pure: aif.json + detail.json (already loaded) -> {relative path:
    content} for every file the skill directory needs. No filesystem access,
    so this is what tests exercise directly.
    """
    slug = _slugify((aif.get("project") or {}).get("name") or "")
    return {
        "SKILL.md": _skill_md(aif, slug),
        "references/overview.md": _overview_md(aif),
        "references/files.md": _files_md(aif),
        "references/relationships.md": _relationships_md(aif),
        "references/detail.json": json.dumps(detail, ensure_ascii=False, indent=2),
    }


def resolve_skill_target(aif: dict, output_dir: str | None = None) -> Path:
    """Where export_skill() would write to for this already-loaded aif --
    output_dir if given, else .claude/skills/<slugified project name>/
    relative to the current working directory, which is where Claude Code
    looks for project-level skills.

    Split out of export_skill() (which still calls this) so a caller can
    inspect what's already at that path *before* export_skill() overwrites
    it there -- cli.py's own collision check (see
    read_existing_skill_project_name()'s docstring) is the one real
    consumer of that today, but this needed to be a named, independently
    testable function either way rather than duplicated inline logic.
    """
    slug = _slugify((aif.get("project") or {}).get("name") or "")
    return Path(output_dir) if output_dir else Path(".claude/skills") / slug


_SKILL_MD_TITLE_RE = re.compile(r"^# (.+) — Ziplex reference$", re.MULTILINE)


def read_existing_skill_project_name(target: Path) -> str | None:
    """The display name embedded in an already-exported SKILL.md's own H1
    heading at `target` (_skill_md()'s own "# {name} -- Ziplex reference"
    line) -- None if there's no SKILL.md there yet, or it doesn't match that
    exact shape (a human-authored skill that happens to occupy the same
    directory, or one from a Ziplex version that predates this format).

    This -- not the slug, and not SKILL.md's own frontmatter `name:` field
    -- is the one signal that can actually tell two *different* source
    projects apart when a collision happens: two differently-named projects
    ("My Project!!!" and "my_project") can both slugify to the identical
    "my-project", so by the time two exports actually collide on the same
    `target`, their slug and frontmatter `name:` are already guaranteed
    identical by construction -- only the free-text display name in the
    heading (never slugified) can still differ.
    """
    try:
        content = (target / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return None
    match = _SKILL_MD_TITLE_RE.search(content)
    return match.group(1) if match else None


def export_skill(aif_path: str, output_dir: str | None = None, aif: dict | None = None) -> str:
    """Loads aif_path (+ its sibling <name>.detail.json, via query_service's
    own _detail_path() rather than re-deriving that convention independently
    here -- one definition of the sibling-file naming, reused, not copied)
    and writes a Claude Agent Skill directory (see resolve_skill_target()
    for exactly where).

    A missing/unreadable detail.json degrades to an empty {} rather than
    failing the whole export -- references/detail.json just comes out
    empty, still a valid (if less useful) skill; the summaries/rules/
    relationships references remain fully usable either way.

    aif: an already-loaded/parsed aif dict, for a caller that has one on
    hand already (cli.py's `_cmd_skill()`, which otherwise would
    re-read/re-parse the same aif_path a third time in one invocation --
    once for its own freshness scope lookup, once for its collision check,
    once more here). aif_path is still required either way, since it's
    also what locates the sibling detail.json -- only the aif_path read
    itself is skipped when this is given. None (the default) reads and
    parses aif_path here exactly as before, including raising the same
    OSError/json.JSONDecodeError a bad path always has.

    No collision check happens here -- an existing directory at the target
    path is always overwritten silently, same as before. cli.py's
    `ziplex skill` command is what optionally warns first (see
    read_existing_skill_project_name()) -- this function stays
    collision-agnostic the same deliberate way it stays freshness-agnostic
    (see this module's own docstring).

    Returns the directory actually written to.
    """
    aif_file = Path(aif_path)
    if aif is None:
        with open(aif_file, "r", encoding="utf-8") as f:
            aif = json.load(f)

    try:
        with open(_detail_path(aif_path), "r", encoding="utf-8") as f:
            detail = json.load(f)
    except (OSError, json.JSONDecodeError):
        detail = {}

    target = resolve_skill_target(aif, output_dir)

    for relative_path, content in generate_skill_files(aif, detail).items():
        out_path = target / relative_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")

    return str(target)
