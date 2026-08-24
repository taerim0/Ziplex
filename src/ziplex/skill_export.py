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

1. **`references/overview.md`** — the AI guide above, coding rules, and token stats (how much smaller this reference is than the raw source).
2. **`references/files.md`** — every file's one-line summary and a confidence score (0.0-1.0). A low score means the summary's wording didn't overlap much with the file's real signatures -- worth a closer look before trusting it, not a guarantee it's wrong.
3. **`references/relationships.md`** — the dependency graph: what each file imports, and (derivable from it) what would be affected by changing one.
4. **`references/detail.json`** — full compressed source per file (structure and signatures kept, function bodies elided), keyed by the same relative path used everywhere else here. Read a file's entry only once its summary/confidence/relationships say it's worth a closer look -- that's the entire point of Ziplex's compression; reading every entry defeats it.
"""


def _overview_md(aif: dict) -> str:
    project = aif.get("project", {})
    rules = aif.get("rules", [])
    tokens = aif.get("tokens", {})

    lines = [
        "# Overview", "",
        f"**Project**: {project.get('name') or '(unnamed)'}",
        f"**Files**: {len(aif.get('files', {}))}", "",
        "## AI guide", "",
        (project.get("prompt") or "").strip() or "(none)", "",
        "## Coding rules", "",
    ]
    lines += [f"- {r}" for r in rules] if rules else ["(none inferred)"]

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
        lines += ["", f"## `{name}`"]
        if internal:
            lines += [f"- depends on: `{d}`" for d in internal]
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


def export_skill(aif_path: str, output_dir: str | None = None) -> str:
    """Loads aif_path (+ its sibling <name>.detail.json, via query_service's
    own _detail_path() rather than re-deriving that convention independently
    here -- one definition of the sibling-file naming, reused, not copied)
    and writes a Claude Agent Skill directory -- output_dir if given, else
    .claude/skills/<slugified project name>/ relative to the current
    working directory, which is where Claude Code looks for project-level
    skills.

    A missing/unreadable detail.json degrades to an empty {} rather than
    failing the whole export -- references/detail.json just comes out
    empty, still a valid (if less useful) skill; the summaries/rules/
    relationships references remain fully usable either way.

    Returns the directory actually written to.
    """
    aif_file = Path(aif_path)
    with open(aif_file, "r", encoding="utf-8") as f:
        aif = json.load(f)

    try:
        with open(_detail_path(aif_path), "r", encoding="utf-8") as f:
            detail = json.load(f)
    except (OSError, json.JSONDecodeError):
        detail = {}

    slug = _slugify((aif.get("project") or {}).get("name") or "")
    target = Path(output_dir) if output_dir else Path(".claude/skills") / slug

    for relative_path, content in generate_skill_files(aif, detail).items():
        out_path = target / relative_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")

    return str(target)
