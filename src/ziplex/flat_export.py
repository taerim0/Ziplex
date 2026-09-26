"""Exports an already-packed aif.json (+ its sibling detail.json) as ONE
flat Markdown file -- summary and compressed body inline together, per
file, instead of aif.json/detail.json's normal split.

Built specifically for the no-tool-access consumption scenario (pasted/
attached into a chat with no file-browsing capability, no MCP server, no
`ziplex detail` to selectively pull one file's body) -- the split aif.json/
detail.json design assumes a consumer can query detail.json for just the
files it actually needs (`ziplex detail`, MCP's get_detail()); with no such
capability, a consumer has to ingest *both* files in full and manually
cross-reference a summary in one to a body in the other. A real,
measured cost: an EXPERIMENTS.md comparison round found this cross-
referencing overhead made aif.json+detail.json together ~67% more
expensive in tokens than a repomix-style single compressed file, for the
same accuracy, on the exact same project -- this module exists to close
that specific, measured gap, not as a guess.

Deliberately a different, additive module from skill_export.py, not an
option bolted onto it -- skill_export.py's own docstring already explains
why *that* format keeps summaries (files.md) and compressed bodies
(references/detail.json) separate: avoiding an always-committed skill
directory bloated with full compressed source. That reasoning doesn't
apply here -- this output is meant to be generated on demand and pasted
somewhere ephemeral, not committed -- so the two modules are free to make
opposite tradeoffs for their own real, different use cases.

Pure generation (generate_flat_markdown) is separate from the I/O wrapper
(export_flat) the same way skill_export.py/packager.py split -- takes
already-loaded aif/detail dicts and returns a single string, so it's
directly testable without touching a filesystem.
"""
from pathlib import Path

from .aif_io import attach_weak_edges
from .query_service import _detail_path
from .confidence import project_confidence_summary

# Common extension -> Markdown fenced-code-block language tag, for a nicer
# (but non-essential) syntax-highlighted read. Deliberately not exhaustive --
# falls back to no language tag (a bare ``` fence still renders fine, just
# unhighlighted) for anything not listed here, rather than trying to keep
# this in lockstep with extract/code/languages.py's own, larger extension
# table (which exists for a completely different purpose: driving Tree-
# sitter compression, not cosmetic Markdown highlighting).
_LANGUAGE_TAGS: dict[str, str] = {
    ".py": "python", ".java": "java", ".ts": "typescript", ".js": "javascript",
    ".tsx": "tsx", ".jsx": "jsx", ".mts": "typescript", ".cts": "typescript", ".mjs": "javascript", ".cjs": "javascript",
    ".lua": "lua", ".mlua": "lua", ".gd": "gdscript", ".go": "go",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".rs": "rust", ".cs": "csharp",
    ".php": "php", ".rb": "ruby", ".sh": "bash", ".bash": "bash",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown",
}


def _language_tag(name: str) -> str:
    return _LANGUAGE_TAGS.get(Path(name).suffix, "")


def _header_md(aif: dict) -> str:
    project = aif.get("project", {})
    rules = aif.get("rules", [])
    files = aif.get("files", {})
    confidence_summary = project_confidence_summary(files)

    lines = [
        f"# {project.get('name') or '(unnamed)'}", "",
        (project.get("prompt") or "").strip() or "(no AI guide generated)", "",
        f"**{len(files)} files** -- confidence {confidence_summary['average']:.2f} average, "
        f"{confidence_summary['needs_review_count']} flagged for review (see each file's own confidence below).",
        "",
    ]

    if rules:
        lines += ["## Coding rules", ""]
        lines += [f"- {r}" for r in rules]
        lines.append("")

    # Same backward-compat guard _overview_md() (skill_export.py) already
    # uses -- an aif.json packed before "folders" existed simply has no
    # such key.
    folders = aif.get("folders")
    if folders:
        lines += ["## Folders", ""]
        for path in sorted(folders):
            info = folders[path]
            summary = (info.get("summary") or "").replace("\n", " ")
            count = info.get("file_count")
            count_text = f"{count} file(s)" if count is not None else "file count unknown"
            lines.append(f"- `{path}` ({count_text}, confidence {info.get('confidence', 1.0):.2f}): {summary}")
        lines.append("")

    return "\n".join(lines)


def _files_md(aif: dict, detail: dict) -> str:
    """The core of the flat format: summary and compressed body together,
    one right after the other, for every file -- the whole point of this
    module (see its own docstring on why the normal split forces a
    no-tool-access consumer to cross-reference two separate structures for
    this exact information).
    """
    files = aif.get("files", {})
    lines = ["## Files", ""]
    for name in sorted(files):
        data = files[name]
        summary = (data.get("summary") or "").strip()
        confidence = data.get("confidence", 1.0)
        flag = " ⚠️ low confidence -- verify against the code below" if confidence < 0.34 else ""
        lines += [f"### `{name}` (confidence: {confidence:.2f}){flag}", "", summary or "(no summary)", ""]

        body = (detail.get(name) or {}).get("compressed")
        if body:
            fence = _fence_for(body)
            lines += [f"{fence}{_language_tag(name)}", body, fence, ""]
    return "\n".join(lines)


def _fence_for(body: str) -> str:
    """A backtick fence longer than any backtick run inside `body` -- a
    Markdown file's compressed body keeps its own ``` code blocks, which
    closed a fixed ``` wrapper early and ran every later file together."""
    longest = run = 0
    for ch in body:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _relationships_md(aif: dict) -> str:
    """One compact line per file that has at least one real dependency --
    unlike skill_export.py's own _relationships_md(), which gives every
    file (including ones with none) a full "## `path`" heading, since that
    verbosity is exactly the kind of per-file overhead this module exists
    to avoid. A file with no dependencies at all is silently omitted
    rather than printed as a "(no dependencies)" line -- there's nothing
    there worth spending tokens on.
    """
    relationships = aif.get("relationships", {})
    lines = ["## Dependency graph", "", "Only files with at least one real dependency are listed.", ""]
    for name in sorted(relationships):
        deps = relationships[name]
        internal = deps.get("internal", [])
        external = deps.get("external", [])
        if not internal and not external:
            continue
        text_refs = set(deps.get("internal_text_refs", []))
        parts = []
        if internal:
            parts.append(", ".join(
                f"{d}{' (text ref)' if d in text_refs else ''}" for d in internal
            ))
        if external:
            parts.append("external: " + ", ".join(external))
        lines.append(f"- `{name}` -> {'; '.join(parts)}")
    return "\n".join(lines) + "\n"


def generate_flat_markdown(aif: dict, detail: dict) -> str:
    """Pure: aif.json + detail.json (already loaded) -> one Markdown
    string. No filesystem access, so this is what tests exercise directly.
    """
    return "\n".join([_header_md(aif), _files_md(aif, detail), _relationships_md(aif)])


def export_flat(aif_path: str, output_path: str | None = None) -> str:
    """I/O wrapper: reads aif_path (+ its sibling <name>.detail.json, via
    query_service's own _detail_path() -- same shared convention
    skill_export.py already reuses rather than re-deriving independently)
    and writes the flat Markdown file. Returns the path written to.

    output_path defaults to aif_path's own stem with a .flat.md suffix,
    next to aif.json -- distinguishable at a glance from aif.json's other
    siblings (<name>.detail.json/<name>.cache.json).
    """
    import json

    with open(aif_path, "r", encoding="utf-8") as f:
        aif = json.load(f)
    detail_path = _detail_path(aif_path)
    with open(detail_path, "r", encoding="utf-8") as f:
        detail = json.load(f)

    markdown = generate_flat_markdown(attach_weak_edges(aif, detail), detail)

    if output_path is None:
        p = Path(aif_path)
        output_path = str(p.with_name(f"{p.stem}.flat.md"))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    return output_path
