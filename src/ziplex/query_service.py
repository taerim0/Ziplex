"""Transport-agnostic read queries over an already-packed project (`aif.json`
plus its sibling `<name>.detail.json`/`<name>.cache.json`).

This is the single core `mcp_server.py` and `gui_server.py` both sit on top
of -- same "N transports, 1 core" shape as `cli.py`/`mcp_server.py` sharing
`edits.py`/`search.py`/`freshness.py` already. Every function here used to
live directly inside `mcp_server.py`'s `@mcp.tool()`-decorated functions;
it moved here once a second transport (the GUI) needed the exact same
logic, so neither transport can drift from the other by editing only one
copy. `mcp_server.py` now registers these functions as MCP tools directly
(`mcp.tool()(get_overview)`, etc.) rather than wrapping them, so a tool's
docstring -- which the MCP SDK reads as the tool description -- lives in
exactly one place too.

Read-only by design -- see `mcp_server.py`'s module docstring for why.
"""

import json
from pathlib import Path

from .file.relationship import (
    get_dependents as _get_dependents,
    get_blast_radius as _get_blast_radius,
)
from .search import search_files, read_detail_range
from .freshness import check_freshness_scoped
from .config import collect_and_scan


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _detail_path(aif_path: str) -> Path:
    """<name>.detail.json sits next to aif.json under the same stem -- the
    convention save_aif() writes to (packager.py). Not configurable, so
    every function that needs detail content just derives it from aif_path.
    """
    p = Path(aif_path)
    return p.with_name(f"{p.stem}.detail.json")


def _cache_path(aif_path: str) -> Path:
    """<name>.cache.json, same sibling-file convention as _detail_path()."""
    p = Path(aif_path)
    return p.with_name(f"{p.stem}.cache.json")


def _stale_warning(project_path: str | None, aif_path: str) -> dict | None:
    """None when project_path isn't given, its cache.json is missing/corrupt,
    or the pack is still fresh; otherwise a compact drift summary to attach
    to a result under "_stale".

    This exists so a caller that always passes project_path can't silently
    keep working off a drifted snapshot without at least being told --
    without it, that requires remembering to call check_freshness as a
    separate step first, which is exactly the kind of step is easy to
    forget. Deliberately wired into get_overview/list_files only (the two
    "orientation" queries a session typically calls first), not into every
    query -- get_detail/get_dependents/get_blast_radius return a bare
    str/list[str] with no room for a sibling field, and re-hashing the whole
    project on every fine-grained follow-up call would add real repeated IO
    for no new information once the first check already surfaced it.
    """
    if project_path is None:
        return None
    try:
        manifest = _load_json(str(_cache_path(aif_path)))
    except (OSError, json.JSONDecodeError):
        return None

    # check_freshness_scoped(), not a bare collect_and_scan()["safe"] --
    # a real bug reported directly (2026-08-26): a file scan_files() flags
    # as sensitive but a human included anyway re-flags as dangerous on
    # every later scan regardless of that earlier decision, so this used to
    # report it "removed" every time get_overview()/list_files() attached
    # this warning, even though check_freshness() below (the standalone
    # `/api/freshness` route) had already been fixed to not make that
    # mistake -- a second call site of the identical bug, missed in that
    # first pass because it lives in this private helper rather than the
    # function the bug was originally reported against. Symptom that made it
    # visible even after the first fix: opening a project showed "changed"
    # for a beat, then the page's own live watcher (gui/watcher.py, fixed
    # correctly the first time) caught up and corrected the badge a moment
    # later. check_freshness_scoped() (freshness.py) now centralizes this
    # exact sequence so a third missed call site can't happen again.
    report = check_freshness_scoped(project_path, manifest)
    if not report.is_stale:
        return None
    return {"is_stale": True, "changed": report.changed, "added": report.added, "removed": report.removed}


def get_overview(aif_path: str, project_path: str | None = None) -> dict:
    """Project name, AI-facing guide, inferred coding rules, and token stats
    for an already-packed project. Call this first -- it's the cheapest,
    always-affordable view of a project, and enough context for many
    questions on its own without fetching any file's detail.

    Pass project_path too (the actual project directory aif_path was packed
    from) and this also runs a free freshness check (a hash comparison, no
    LLM calls, same as the standalone check_freshness query) -- if the pack
    has drifted from disk, the result carries an extra "_stale" field
    listing what changed/was added/was removed, so a re-pack is worth
    considering before trusting the rest. Omitting project_path just skips
    that check; nothing else about the result changes.
    """
    aif = _load_json(aif_path)
    result = {
        "project": aif.get("project", {}),
        "rules": aif.get("rules", []),
        "tokens": aif.get("tokens", {}),
        "file_count": len(aif.get("files", {})),
    }
    warning = _stale_warning(project_path, aif_path)
    if warning:
        result["_stale"] = warning
    return result


def list_files(aif_path: str, project_path: str | None = None) -> dict:
    """Every file in the project mapped to its one-line summary and a
    heuristic confidence score (0.0-1.0, see src/confidence.py) for how well
    that summary's wording actually matches the file's extracted signatures
    -- not a correctness guarantee, but a low score is a real reason to
    fetch get_detail and check for yourself before trusting the summary.
    Use this to decide which file (if any) is worth that closer look --
    summaries are already loaded here at effectively no cost; full source
    is not.

    Pass project_path too for the same free "_stale" freshness check
    get_overview() does -- see its docstring for details.
    """
    aif = _load_json(aif_path)
    result = {
        name: {"summary": data.get("summary", ""), "confidence": data.get("confidence", 1.0)}
        for name, data in aif.get("files", {}).items()
    }
    warning = _stale_warning(project_path, aif_path)
    if warning:
        result["_stale"] = warning
    return result


def get_folders(aif_path: str) -> dict:
    """{folder path: {"summary": "..."}} for every folder that directly
    contains at least one collected file -- the aggregate-level counterpart
    to list_files()'s per-file summaries, generated by
    folder_summary.py during pack(). A root-level file's folder is "."
    (Path.parent's own natural value, not a special-cased sentinel).

    A project packed before this field existed (aif.json has no "folders"
    key at all) returns {} rather than raising -- same backward-compat
    default `tech_stack`'s own section in skill_export.py already uses for
    the same reason.
    """
    aif = _load_json(aif_path)
    return aif.get("folders", {})


def get_relationships(aif_path: str) -> dict:
    """The whole dependency graph at once -- every file mapped to what it
    depends on internally (other project files) and externally (packages),
    aif.json's `relationships` field verbatim. get_dependents()/
    get_blast_radius() answer a question about one file; this is the same
    underlying graph with nothing filtered out, for a caller that wants the
    project's overall shape in one call (e.g. a whole-tree browser view)
    instead of walking it file by file.
    """
    aif = _load_json(aif_path)
    return aif.get("relationships", {})


def get_dependents(aif_path: str, file: str) -> list[str]:
    """Files that directly depend on `file` -- who would need a second look
    if `file` changes. `file` is a key from list_files()'s result.
    """
    aif = _load_json(aif_path)
    return _get_dependents(aif.get("relationships", {}), file)


def get_blast_radius(aif_path: str, file: str) -> list[str]:
    """Every file affected by a change to `file`, directly or transitively --
    the full impact set, not just its immediate dependents. Built on the
    same human-corrected dependency graph as get_dependents(), which is why
    this is worth calling instead of guessing from imports yourself.
    """
    aif = _load_json(aif_path)
    return _get_blast_radius(aif.get("relationships", {}), file)


def get_detail(aif_path: str, file: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """The compressed source for one file -- structure and signatures kept,
    function bodies elided. Fetch this only once a summary or a dependents/
    blast-radius query says `file` is actually worth a closer look; it costs
    far more tokens than the summary every other query here returns. Pass
    start_line/end_line (1-based, inclusive) to read part of a large file
    instead of the whole thing.
    """
    detail = _load_json(str(_detail_path(aif_path)))
    entry = detail.get(file)
    if entry is None:
        raise ValueError(f"{file!r} not found in {_detail_path(aif_path)}")
    return read_detail_range(entry.get("compressed", ""), start_line, end_line)


def check_freshness(project_path: str, aif_path: str) -> dict:
    """Checks whether aif_path's pack is still current relative to
    project_path's actual files on disk -- a hash comparison, no LLM calls
    and no re-extraction, so it's cheap enough to call before trusting
    get_overview/list_files/get_dependents/get_blast_radius/get_detail on a
    project you suspect has changed since it was last packed. (search_project
    never needs this -- it always reads files live, never aif.json/
    detail.json.) Reports which files changed, were added, were removed, or
    are unchanged since the pack aif_path came from; doesn't fix anything
    itself -- a stale result still means re-running `pack`.

    Re-collects via project_path's own .ziplex.json (config.py) the same way
    pack() itself would -- otherwise a project scoped with include/ignore
    patterns would get diffed against its *unscoped* full file tree here,
    reporting every out-of-scope file as spuriously "added" even
    immediately after a fresh pack.

    check_freshness_scoped() (freshness.py) folds a previously-included
    dangerous file back into the comparison set -- without it, a file a
    human opted to include anyway despite scan_files() flagging it
    (file/selector.py's review_dangerous_files(), the GUI's "include
    anyway" checkbox, or a `preselected` caller naming it directly) gets
    re-flagged as dangerous on every later scan regardless of that earlier
    decision, dropped from `collect_and_scan()`'s own "safe" list every
    time, and would otherwise be reported here as permanently `removed`
    even though it's unchanged and still on disk -- a real bug reported
    directly (2026-08-26).
    """
    manifest = _load_json(str(_cache_path(aif_path)))
    report = check_freshness_scoped(project_path, manifest)
    return {
        "is_stale": report.is_stale,
        "changed": report.changed,
        "added": report.added,
        "removed": report.removed,
        "unchanged": report.unchanged,
    }


def search_project(project_path: str, pattern: str, context_lines: int = 0, ignore_case: bool = False) -> list[dict]:
    """Regex search across the project's original files -- use this when you
    don't already know which file has what you're after (get_detail needs a
    filename; this doesn't). Unlike the other queries here, this doesn't read
    aif.json/detail.json at all: it re-collects and re-security-scans the
    project fresh on every call, straight from project_path, so results are
    always current even if aif.json is stale and secrets are still filtered
    even if the project changed since the last pack. Also respects
    project_path's own .ziplex.json include/ignore (config.py), the same
    scope pack() itself would use -- a file deliberately excluded from
    packing shouldn't turn up in search results either.
    """
    safe_files = collect_and_scan(project_path)["safe"]
    matches = search_files(safe_files, project_path, pattern, context_lines, ignore_case)
    return [
        {
            "file": m.file,
            "line": m.line_number,
            "text": m.line,
            "context_before": m.context_before,
            "context_after": m.context_after,
        }
        for m in matches
    ]
