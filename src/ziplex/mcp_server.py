"""Ziplex MCP server, two modes chosen in main():

- v2 (default): `ziplex-mcp [--project PATH]` -- five graph tools over the
  live SQLite index (`ziplex.core.api.Graph`), compact text answers. Every
  call refreshes the index first, throttled to one disk walk per
  REFRESH_INTERVAL; main() starts the cold build on a background thread.
- legacy: `--aif PATH` or `--legacy-tools` -- the ten read-only tools over an
  already-packed `aif.json` (+ `<name>.detail.json`/`<name>.cache.json`).
  Each wraps the matching `query_service` function, making
  `aif_path`/`project_path` optional (defaults from `--aif`/`--project`).
  Descriptions come from TOOL_DESCRIPTIONS, assigned to `__doc__` before
  `server.tool()` registers the wrapper (it reads `__doc__` at registration).

    claude mcp add ziplex -- ziplex-mcp --project .
    claude mcp add ziplex -- ziplex-mcp --aif .ziplex/Ziplex.json --project .

Tool lists and instructions are paid for in every session: keep them short
(budget tests in tests/test_mcp_server.py).
"""

import argparse
import asyncio
import os
import threading
import time

from mcp.server import MCPServer

from . import query_service
from . import __version__
from .core import api

# ---- legacy mode (aif.json snapshot) ----------------------------------------

# Sent once at connect. Agents ignore an optional graph unless told which
# questions it answers (EXPERIMENTS #10), so say so up front -- briefly, since
# every token here is paid in every session.
INSTRUCTIONS = """Ziplex serves a packed snapshot of this project with import resolution already done (relative/index files, tsconfig aliases, workspace packages, Python packages, Go packages). For dependency questions use it instead of grepping imports: get_dependents / get_blast_radius (who imports X, what breaks if X changes), get_relationships (what X imports), get_graph_summary (hubs, cycles, folder layering). Use Grep/Read for text search and reading code. A "_stale" field lists files changed since packing."""

# Tool descriptions are the MCP-facing contract, kept short on purpose: the
# full tool list is sent to the client in every session. query_service's own
# docstrings stay the developer documentation.
TOOL_DESCRIPTIONS = {
    "get_overview": "Project name, AI guide, rules, tech stack, file/folder counts. "
    "With project_path, adds `_stale` if files changed since packing.",
    "list_files": "Per-file summaries. Scope with folder= (\".\" for root) or "
    "confidence_below=; unscoped output is large on big projects.",
    "get_folders": "One-line summary and file count per multi-file folder.",
    "get_relationships": "What the given files import: {file: {internal, external}}. "
    "Pass files= to scope; unscoped output is large.",
    "get_graph_summary": "Whole-graph facts: most-depended-on files, files with most "
    "dependencies, import cycles, orphans, folder->folder edges.",
    "get_dependents": "Files that directly import `file`. include_text_refs=true also "
    "counts prose mentions (READMEs, configs).",
    "get_blast_radius": "Every file affected by changing `file`, transitively "
    "(reverse dependencies). include_text_refs as in get_dependents.",
    "get_detail": "Compressed source of one file (bodies elided), optionally a line "
    "range. Line numbers do not match the original; Read the real file to edit.",
    "check_freshness": "Which packed files changed/were added/removed on disk since packing.",
    "search_project": "Regex search over the project's original files. Prefer Grep "
    "when available.",
}

# Set once by main() from --aif/--project, read fresh by every wrapper
# below on every call -- never baked in at import time, so main() setting
# these after the tools below are already registered still works. A
# caller's own explicit aif_path/project_path always wins over these.
# Stay None for anything that imports this module without ever calling
# main() (tests, a bare `from ziplex import mcp_server`) -- a wrapper with
# no explicit path and no default then raises a clear error instead of
# silently resolving to nothing.
_defaults: dict[str, str | None] = {"aif": None, "project": None}


def _resolve_aif(aif_path: str | None) -> str:
    resolved = aif_path if aif_path is not None else _defaults["aif"]
    if resolved is None:
        raise ValueError(
            "aif_path is required: pass it explicitly, or start ziplex-mcp "
            "with --aif <path> to set a default for this server."
        )
    return resolved


def _resolve_project_required(project_path: str | None) -> str:
    resolved = project_path if project_path is not None else _defaults["project"]
    if resolved is None:
        raise ValueError(
            "project_path is required: pass it explicitly, or start "
            "ziplex-mcp with --project <path> to set a default for this "
            "server."
        )
    return resolved


def _resolve_project_optional(project_path: str | None) -> str | None:
    # get_overview/list_files treat a missing project_path as "skip the
    # freshness check," a legitimate final answer -- unlike every other
    # tool here, where a missing project_path is an unset default, not a
    # meaningful value in its own right.
    return project_path if project_path is not None else _defaults["project"]


def _resolve_aif_optional(aif_path: str | None) -> str | None:
    # search_project()'s own aif_path is the same shape as project_path
    # above -- optional, since search_project works with no pack at all;
    # a missing value just means "no pack-specific --include/--ignore scope
    # to layer on top of .ziplex.json," not an unset required default.
    return aif_path if aif_path is not None else _defaults["aif"]


def get_overview(*, aif_path: str | None = None, project_path: str | None = None) -> dict:
    return query_service.get_overview(_resolve_aif(aif_path), _resolve_project_optional(project_path))


get_overview.__doc__ = TOOL_DESCRIPTIONS["get_overview"]


def list_files(
    *,
    aif_path: str | None = None,
    project_path: str | None = None,
    folder: str | None = None,
    confidence_below: float | None = None,
) -> dict:
    return query_service.list_files(
        _resolve_aif(aif_path), _resolve_project_optional(project_path), folder, confidence_below
    )


list_files.__doc__ = TOOL_DESCRIPTIONS["list_files"]


def get_folders(*, aif_path: str | None = None) -> dict:
    return query_service.get_folders(_resolve_aif(aif_path))


get_folders.__doc__ = TOOL_DESCRIPTIONS["get_folders"]


def get_relationships(
    *, aif_path: str | None = None, files: list[str] | None = None, include_text_refs: bool = False
) -> dict:
    return query_service.get_relationships(_resolve_aif(aif_path), files, include_text_refs=include_text_refs)


get_relationships.__doc__ = TOOL_DESCRIPTIONS["get_relationships"]


def get_graph_summary(
    *, aif_path: str | None = None, include_text_refs: bool = False, top_n: int = 10, code_only: bool = True
) -> dict:
    return query_service.get_graph_summary(
        _resolve_aif(aif_path), include_text_refs=include_text_refs, top_n=top_n, code_only=code_only
    )


get_graph_summary.__doc__ = TOOL_DESCRIPTIONS["get_graph_summary"]


def get_dependents(*, aif_path: str | None = None, file: str, include_text_refs: bool = False) -> list[str]:
    return query_service.get_dependents(_resolve_aif(aif_path), file, include_text_refs=include_text_refs)


get_dependents.__doc__ = TOOL_DESCRIPTIONS["get_dependents"]


def get_blast_radius(*, aif_path: str | None = None, file: str, include_text_refs: bool = False) -> list[str]:
    return query_service.get_blast_radius(_resolve_aif(aif_path), file, include_text_refs=include_text_refs)


get_blast_radius.__doc__ = TOOL_DESCRIPTIONS["get_blast_radius"]


def get_detail(
    *,
    aif_path: str | None = None,
    file: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    return query_service.get_detail(_resolve_aif(aif_path), file, start_line, end_line)


get_detail.__doc__ = TOOL_DESCRIPTIONS["get_detail"]


def check_freshness(*, project_path: str | None = None, aif_path: str | None = None) -> dict:
    return query_service.check_freshness(_resolve_project_required(project_path), _resolve_aif(aif_path))


check_freshness.__doc__ = TOOL_DESCRIPTIONS["check_freshness"]


def search_project(
    *,
    project_path: str | None = None,
    aif_path: str | None = None,
    pattern: str,
    context_lines: int = 0,
    ignore_case: bool = False,
    max_results: int | None = query_service.DEFAULT_SEARCH_MAX_RESULTS,
) -> dict:
    return query_service.search_project(
        _resolve_project_required(project_path),
        pattern,
        context_lines,
        ignore_case,
        max_results,
        aif_path=_resolve_aif_optional(aif_path),
    )


search_project.__doc__ = TOOL_DESCRIPTIONS["search_project"]


def build_legacy_server() -> MCPServer:
    server = MCPServer("ziplex", instructions=INSTRUCTIONS)
    for name in TOOL_DESCRIPTIONS:
        server.tool()(globals()[name])
    return server


# ---- v2 mode (live graph index) -------------------------------------------

INSTRUCTIONS_V2 = """Ziplex keeps a live import graph of this repo (relative/index files, tsconfig aliases, workspace packages, Python and Go packages resolved), refreshed on every call. For structural questions use it instead of grepping imports: who imports X / what breaks if X changes (who_depends_on), what X imports (dependencies_of), which tests cover a change (impacted_tests), how A reaches B (dependency_path), hubs, cycles and folder layering (graph_overview). Use Grep/Read for text search and reading code. After editing files, call impacted_tests before finishing."""

TOOL_DESCRIPTIONS_V2 = {
    "graph_overview": "Repo or folder (scope=\"src/api\") structure: file/test/edge counts, most-depended-on "
    "hubs, files with most imports, import cycles, folder->folder layering, unresolved relative imports.",
    "who_depends_on": "Files that import the given files (path or unique suffix), grouped by hop up to depth, "
    "with import line numbers and test counts. include_mentions adds prose references (docs, configs).",
    "dependencies_of": "What the given files import: internal files with line numbers, prose mentions, "
    "and (external=true) third-party modules.",
    "impacted_tests": "Tests reached from changed files over reverse imports (plus conftest/same-package), "
    "with the chain and a ready-to-run command. Pass files, or diff=worktree|staged|<git rev or range>; "
    "no arguments = uncommitted worktree changes.",
    "dependency_path": "Shortest import chain from source to target, with the import line of each hop.",
}

REFRESH_INTERVAL = 2.0   # seconds a refresh result is reused before walking disk again
INDEX_WAIT = 20.0        # how long a call waits for a running refresh before giving up
BUSY_MESSAGE = "indexing in progress (first build of a large repo takes ~10-20s), retry shortly"


class GraphSession:
    """One Graph plus a throttled, thread-safe refresh. The lock serializes
    refreshes and queries (they share one SQLite connection)."""

    def __init__(self, project_path: str, db_path: str | None = None):
        self.graph = api.Graph(project_path, db_path=db_path)
        self._lock = threading.Lock()
        self._refreshed_at: float | None = None   # time.monotonic() of the last finished refresh
        self._generation: str | None = None

    def start_background_refresh(self) -> threading.Thread:
        """Runs the first refresh off the request path; calls made meanwhile
        wait on the lock (up to INDEX_WAIT). A failure is left for the next
        call, which retries and reports it."""
        self._lock.acquire()

        def run():
            try:
                self._refresh_locked()
            except Exception:
                pass
            finally:
                self._lock.release()

        thread = threading.Thread(target=run, name="ziplex-index", daemon=True)
        thread.start()
        return thread

    def _refresh_locked(self) -> None:
        result = self.graph.refresh()
        self._generation = str(result["generation"])
        self._refreshed_at = time.monotonic()

    def run(self, query) -> str:
        """Refreshes (throttled), runs `query(graph) -> str` and appends the
        status line. Bad arguments come back as text, never an exception."""
        if not self._lock.acquire(timeout=INDEX_WAIT):
            return BUSY_MESSAGE
        try:
            age = None if self._refreshed_at is None else time.monotonic() - self._refreshed_at
            if age is None or age >= REFRESH_INTERVAL:
                try:
                    self._refresh_locked()
                except Exception as e:
                    return f"error: index refresh failed: {type(e).__name__}: {e}"
                age = None
            try:
                body = query(self.graph)
            except ValueError as e:  # unknown/ambiguous file, bad git rev
                body = f"error: {e}"
            freshness = "fresh" if age is None else f"fresh as of {age:.1f}s ago"
            return f"{body}\n[index gen {self._generation}, {freshness}]"
        finally:
            self._lock.release()


def build_v2_server(session: GraphSession) -> MCPServer:
    server = MCPServer("ziplex", instructions=INSTRUCTIONS_V2)

    def tool(fn):
        server.tool(description=TOOL_DESCRIPTIONS_V2[fn.__name__], structured_output=False)(fn)
        return fn

    async def call(query) -> str:
        # Off the event loop: a refresh can take seconds.
        return await asyncio.to_thread(session.run, query)

    @tool
    async def graph_overview(scope: str = "") -> str:
        return await call(lambda g: api.render_overview(g.overview(scope)))

    @tool
    async def who_depends_on(
        files: list[str], depth: int = 1, include_mentions: bool = False, limit: int = 60
    ) -> str:
        return await call(lambda g: api.render_dependents(g.dependents(files, depth, include_mentions), limit))

    @tool
    async def dependencies_of(files: list[str], external: bool = False) -> str:
        return await call(lambda g: api.render_dependencies(g.dependencies(files, external)))

    @tool
    async def impacted_tests(files: list[str] = [], diff: str = "") -> str:
        spec = diff.strip() or (None if files else "worktree")
        return await call(lambda g: api.render_impacted(g.impacted_tests(files, spec)))

    @tool
    async def dependency_path(source: str, target: str) -> str:
        def query(g):
            a, b = g.resolve(source), g.resolve(target)
            return api.render_path(a, b, g.path(a, b))
        return await call(query)

    return server


def build_server(mode: str = "v2", project: str | None = None, db_path: str | None = None) -> MCPServer:
    """`mode` is "v2" or "legacy". A v2 server carries its GraphSession as
    `server.ziplex_session`; without start_background_refresh() the first
    call does the first refresh itself."""
    if mode == "legacy":
        return build_legacy_server()
    session = GraphSession(project or os.getcwd(), db_path=db_path)
    server = build_v2_server(session)
    server.ziplex_session = session
    return server


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Ziplex MCP server")
    parser.add_argument("--version", action="version", version=f"ziplex-mcp {__version__}")
    parser.add_argument(
        "--project",
        default=None,
        help="Project to serve (v2; default: current directory). Legacy: default project_path for tool calls.",
    )
    parser.add_argument(
        "--aif",
        default=None,
        help="Selects legacy mode: serve this packed aif.json (default aif_path for tool calls).",
    )
    parser.add_argument(
        "--legacy-tools", action="store_true", help="Serve the ten aif.json tools instead of the live graph tools.",
    )
    args = parser.parse_args(argv)
    if args.aif or args.legacy_tools:
        _defaults["aif"] = args.aif
        _defaults["project"] = args.project
        server = build_server("legacy")
    else:
        server = build_server("v2", project=args.project)
        server.ziplex_session.start_background_refresh()
    server.run()


if __name__ == "__main__":
    main()
