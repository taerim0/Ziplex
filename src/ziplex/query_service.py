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
from .file.textutil import parent_folder
from .search import search_files, read_detail_range
from .freshness import check_freshness_scoped, load_pack_scope, cache_path_for_aif
from .config import collect_and_scan


def _load_json(path: str) -> dict:
    """Shared read path for every function below that loads an already-
    produced JSON file (aif.json/detail.json/cache.json) by path.

    Re-raises FileNotFoundError/JSONDecodeError with a clearer, actionable
    message instead of letting the bare open()/json.load() one through
    as-is -- cli.py got the equivalent fix for its own file-loading commands
    (`_load_json_or_exit()`) well before this did; that fix stayed CLI-only
    since it also prints a friendly Korean message and calls sys.exit(1),
    neither of which fits this shared, transport-agnostic layer. What *does*
    fit here: gui_server.py already has generic `@app.errorhandler(OSError)`/
    `@app.errorhandler(json.JSONDecodeError)` handlers that surface
    `str(exception)` (falling back to it once `.filename`/`.strerror` come up
    empty, which they do for the single-string-arg re-raise below), and the
    MCP SDK does the same for an uncaught exception from a tool call -- so
    both transports automatically pick up the better message from this one
    change, with no per-route/per-tool code of their own. Confirmed live via
    a real stdio MCP call before this fix: a missing aif_path came back as
    the raw `[Errno 2] No such file or directory: '...'` text, no more
    actionable to the calling agent than it would be to a human reading a
    raw traceback.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"{path} not found -- check the path, or run `ziplex pack` on the "
            "project first to create it (aif.json/detail.json/cache.json are "
            "only ever produced together, as siblings of each other)"
        ) from e
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"{path} is not valid JSON ({e.msg}) -- it may be corrupted, "
            "still being written by an in-progress pack, or not a Ziplex "
            "output file at all",
            e.doc,
            e.pos,
        ) from e


def _detail_path(aif_path: str) -> Path:
    """<name>.detail.json sits next to aif.json under the same stem -- the
    convention save_aif() writes to (packager.py). Not configurable, so
    every function that needs detail content just derives it from aif_path.
    """
    p = Path(aif_path)
    return p.with_name(f"{p.stem}.detail.json")


def _cache_path(aif_path: str) -> Path:
    """<name>.cache.json, same sibling-file convention as _detail_path().
    Thin wrapper over freshness.cache_path_for_aif() -- the actual
    convention lives there now, shared with cli.py, so it can't drift
    between the two (a real duplication risk caught by code review).
    """
    return cache_path_for_aif(aif_path)


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
    extra_include, extra_ignore = load_pack_scope(aif_path)
    report = check_freshness_scoped(project_path, manifest, extra_include, extra_ignore)
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


def list_files(
    aif_path: str,
    project_path: str | None = None,
    folder: str | None = None,
    confidence_below: float | None = None,
) -> dict:
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

    Two optional filters, composable with each other: `folder` scopes the
    result to files directly inside one folder (get_folders()'s own path
    convention -- "." for root-level files, trailing slashes and "" both
    normalized to match it, and a '\\'-separated path normalized to '/' the
    same way get_dependents()/get_blast_radius() do) instead of every file
    in the project, for drilling into one folder after get_folders() names
    it worth a closer look -- an unrecognized folder just returns {} rather
    than raising, unlike a bad `file` in get_dependents()/get_blast_radius():
    a caller filtering here is narrowing a set of folders it can already see
    the keys of (typically via get_folders()), not looking one up blind.
    `confidence_below` returns only files with a stored confidence
    strictly under the given cutoff -- pass confidence.REVIEW_THRESHOLD
    (0.34) for the same triage corrector.py already applies in its own
    human-review loop, or any other cutoff a caller wants, now reachable
    here without first fetching every file's summary just to filter
    client-side. Neither filter touches the "_stale" check, which always
    looks at the whole project regardless of what's being asked for.

    Both exist for the same reason search_project() got a max_results cap
    and check_freshness() dropped its unchanged file list: measured
    directly against Ziplex's own 115-file self-pack, an unscoped call here
    costs ~5,000 tokens -- the same "response grows with project size"
    problem those two already had fixed for them, left open on this tool
    until now. Opt-in rather than a forced cap, unlike search_project's,
    since there's no single "too many files" threshold that fits every
    project size the way an arbitrary regex match count has one.
    """
    if folder is not None:
        folder = _normalize_path_arg(folder).rstrip("/") or "."
    aif = _load_json(aif_path)
    result = {}
    for name, data in aif.get("files", {}).items():
        if folder is not None and parent_folder(name) != folder:
            continue
        confidence = data.get("confidence", 1.0)
        if confidence_below is not None and confidence >= confidence_below:
            continue
        result[name] = {"summary": data.get("summary", ""), "confidence": confidence}
    warning = _stale_warning(project_path, aif_path)
    if warning:
        result["_stale"] = warning
    return result


def _normalize_path_arg(value: str) -> str:
    """Every key this module compares a caller-supplied path against
    (`files`/`relationships`) is always POSIX-style (`/`), the convention
    the whole pipeline normalizes to before anything reaches aif.json -- but
    nothing on the *reading* side enforced that on a caller-supplied `file`/
    `folder` argument. A backslash path (natural to type on Windows, and a
    real risk here specifically since this project is developed on Windows)
    silently failed to match any real key instead of erroring -- see
    get_dependents()/get_blast_radius()'s own comment for why that's worse
    than raising outright.
    """
    return value.replace("\\", "/")


def _require_known_file(relationships: dict, file: str, aif_path: str) -> str:
    """Normalizes `file` and confirms it's a real key in `relationships`
    before either get_dependents()/get_blast_radius() below ever calls into
    file/relationship.py's own graph traversal -- build_tree() (packager.py)
    gives *every* packed file an entry there (even one with empty internal/
    external lists), so "not a key at all" reliably means an unrecognized
    name (a typo, a backslash path on Windows, a file that was never
    packed), never a legitimate "this file really has zero dependents"
    answer -- that case is instead an empty list from a real key, which
    both callers already return correctly.

    Real bug this closes: get_dependents(relationships, "some\\bad\\path")
    and a genuine typo like "backend/modle.py" both silently returned []
    before this check existed -- indistinguishable from "no dependents,
    safe to change," the single worst wrong answer these two tools could
    give an agent deciding whether a change is safe. get_detail() already
    raises the equivalent ValueError for an unrecognized file; these two
    tools didn't, purely by oversight, not by design -- confirmed live via
    a real stdio MCP call before this fix (a Windows-style backslash path
    and a misspelled filename each returned isError: false with an empty
    result, identical to a real leaf file's correct answer).
    """
    file = _normalize_path_arg(file)
    if file not in relationships:
        raise ValueError(
            f"{file!r} not found in {aif_path}'s relationships -- "
            "check list_files() or get_folders() for the real path (paths are "
            "relative to the project root and always use '/', never '\\')"
        )
    return file


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


def get_relationships(aif_path: str, files: list[str] | None = None) -> dict:
    """The whole dependency graph at once -- every file mapped to what it
    depends on internally (other project files) and externally (packages),
    aif.json's `relationships` field verbatim. get_dependents()/
    get_blast_radius() answer a question about one file; this is the same
    underlying graph with nothing filtered out, for a caller that wants the
    project's overall shape in one call (e.g. a whole-tree browser view)
    instead of walking it file by file.

    Pass `files` to scope the result to just those keys (each one's full
    internal/external/internal_text_refs entry, not filtered further)
    instead of the whole project -- the same "response grows with project
    size" problem list_files()'s own folder/confidence_below params
    address, and the more expensive of the two: measured directly against
    Ziplex's own 115-file self-pack, an unscoped call here costs ~9,400
    tokens, the single priciest of the nine tools. Each name is normalized
    ('\\' -> '/') the same way get_dependents()/get_blast_radius() do,
    since it's matched against relationships' own '/'-separated keys. A
    name not present in `relationships` (after normalization) is silently
    skipped rather than raising -- a caller filtering here is narrowing a
    graph it can already see the keys of (typically via list_files()), not
    looking one up blind the way get_detail() does.
    """
    aif = _load_json(aif_path)
    relationships = aif.get("relationships", {})
    if files is None:
        return relationships
    normalized = (_normalize_path_arg(name) for name in files)
    return {name: relationships[name] for name in normalized if name in relationships}


def get_dependents(aif_path: str, file: str, include_text_refs: bool = True) -> list[str]:
    """Files that directly depend on `file` -- who would need a second look
    if `file` changes. `file` is a key from list_files()'s result. Paths use
    '/' regardless of OS; a raw Windows-style '\\' path is normalized before
    matching. Raises ValueError if `file` isn't a real file in this project
    -- an empty result means "recognized, genuinely zero dependents," never
    "not found" (see _require_known_file()'s docstring for why that
    distinction matters).

    include_text_refs=False excludes a dependent whose only link to `file`
    is a filename mentioned in prose (a README, a config value) rather than
    an actual import or a structural reference (e.g. a Godot scene's
    ext_resource path) -- see file/relationship.py's `get_dependents()` and
    text_references.py for what counts as which.
    """
    aif = _load_json(aif_path)
    relationships = aif.get("relationships", {})
    file = _require_known_file(relationships, file, aif_path)
    return _get_dependents(relationships, file, include_text_refs=include_text_refs)


def get_blast_radius(aif_path: str, file: str, include_text_refs: bool = True) -> list[str]:
    """Every file affected by a change to `file`, directly or transitively --
    the full impact set, not just its immediate dependents. Built on the
    same human-corrected dependency graph as get_dependents(), which is why
    this is worth calling instead of guessing from imports yourself. Same
    path-normalization and not-found behavior as get_dependents() -- see its
    docstring.

    include_text_refs is get_dependents()'s own param -- see its docstring.
    """
    aif = _load_json(aif_path)
    relationships = aif.get("relationships", {})
    file = _require_known_file(relationships, file, aif_path)
    return _get_blast_radius(relationships, file, include_text_refs=include_text_refs)


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
    immediately after a fresh pack. `load_pack_scope()` additionally reads back
    aif_path's own `project.scope` for the one-off --include/--ignore CLI
    extras that specific pack ran with on top of .ziplex.json -- unlike
    .ziplex.json, those aren't reproducible from disk on their own, so
    without this a project packed with `pack --include ...` would still hit
    the same spurious-"added" problem this docstring already describes,
    just for the CLI-only part of its scope.

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
    extra_include, extra_ignore = load_pack_scope(aif_path)
    report = check_freshness_scoped(project_path, manifest, extra_include, extra_ignore)
    return {
        "is_stale": report.is_stale,
        "changed": report.changed,
        "added": report.added,
        "removed": report.removed,
        # A count, not the full file list: report.unchanged has no caller
        # here that actually needs the names (freshness.py's own
        # load_previous_summaries() reads FreshnessReport.unchanged
        # directly for its cache-reuse logic -- a completely separate,
        # internal consumer this dict has nothing to do with), and the GUI
        # never rendered it either -- measured directly against a real
        # 107-file project: the full list was 65% of this whole response
        # (556 of 850 tokens) for zero actual use on the receiving end.
        "unchanged_count": len(report.unchanged),
    }


# Default cap for search_project()'s match count -- see its own docstring.
# Not applied to search_files()/the CLI's `search` subcommand, which have
# no such cap by default.
DEFAULT_SEARCH_MAX_RESULTS = 50


def search_project(
    project_path: str,
    pattern: str,
    context_lines: int = 0,
    ignore_case: bool = False,
    max_results: int | None = DEFAULT_SEARCH_MAX_RESULTS,
    aif_path: str | None = None,
) -> dict:
    """Regex search across the project's original files -- use this when you
    don't already know which file has what you're after (get_detail needs a
    filename; this doesn't). Unlike the other queries here, this doesn't read
    aif.json/detail.json for the search itself: it re-collects and
    re-security-scans the project fresh on every call, straight from
    project_path, so results are always current even if aif.json is stale
    and secrets are still filtered even if the project changed since the
    last pack. Also respects project_path's own .ziplex.json include/ignore
    (config.py), the same scope pack() itself would use -- a file
    deliberately excluded from packing shouldn't turn up in search results
    either.

    Pass aif_path too (optional -- this still works with no pack at all) to
    additionally respect a one-off `pack --include`/`--ignore` CLI extra
    that specific pack ran with, on top of .ziplex.json -- unlike
    .ziplex.json, those extras aren't reproducible from disk on their own,
    only recorded in that pack's own aif.json `project.scope` field (see
    freshness.load_pack_scope()'s docstring). A real gap this closes:
    without aif_path, a file excluded via `pack --ignore "**/*.generated.*"`
    still turned up in search results here even though check_freshness()
    already correctly excluded it from its own comparison -- the same class
    of bug, just missed on this tool specifically since it never took
    aif_path to look the scope up from. Omitting aif_path searches under
    .ziplex.json's own scope alone, same as before.

    Capped at max_results matches by default (DEFAULT_SEARCH_MAX_RESULTS,
    currently 50) -- a broad or common pattern against a real project can
    otherwise return hundreds of matches in one call (measured directly: a
    single common word against a 47-file project returned 204 matches,
    ~9,000 tokens, uncapped). The scan itself stops early once the cap is
    hit, not just the returned list -- narrow `pattern` first if you
    actually need more than a glance. Pass
    max_results=None for the old unlimited behavior. The result's
    "truncated" flag is True when there were more matches than fit --
    narrow `pattern` (or raise max_results) and call again rather than
    assuming "matches" is the complete picture.
    """
    extra_include, extra_ignore = load_pack_scope(aif_path) if aif_path else (None, None)
    safe_files = collect_and_scan(project_path, extra_include, extra_ignore)["safe"]
    if max_results is None:
        raw_matches = search_files(safe_files, project_path, pattern, context_lines, ignore_case)
        truncated = False
    else:
        # Ask for one more than the cap so truncation can be detected from
        # this single scan, without a second full pass just to count what
        # was left out.
        raw_matches = search_files(safe_files, project_path, pattern, context_lines, ignore_case, max_results + 1)
        truncated = len(raw_matches) > max_results
        raw_matches = raw_matches[:max_results]
    return {
        "matches": [
            {
                "file": m.file,
                "line": m.line_number,
                "text": m.line,
                "context_before": m.context_before,
                "context_after": m.context_after,
            }
            for m in raw_matches
        ],
        "truncated": truncated,
    }
