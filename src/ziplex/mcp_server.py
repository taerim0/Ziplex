"""Ziplex MCP server: read-only tools over an already-packed project
(`aif.json` + sibling `<name>.detail.json`/`<name>.cache.json`).

Each tool wraps the matching `query_service` function; the wrapper only makes
`aif_path`/`project_path` optional, defaulting to `--aif`/`--project` from
main(). Descriptions come from TOOL_DESCRIPTIONS and must be assigned before
`mcp.tool()` registers the wrapper (it reads `__doc__` at registration).

    ziplex-mcp --aif .ziplex/Ziplex.json --project .
    claude mcp add ziplex -- ziplex-mcp --aif .ziplex/Ziplex.json --project .
"""

import argparse

from mcp.server import MCPServer

from . import query_service
from . import __version__

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

mcp = MCPServer("ziplex", instructions=INSTRUCTIONS)

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
mcp.tool()(get_overview)


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
mcp.tool()(list_files)


def get_folders(*, aif_path: str | None = None) -> dict:
    return query_service.get_folders(_resolve_aif(aif_path))


get_folders.__doc__ = TOOL_DESCRIPTIONS["get_folders"]
mcp.tool()(get_folders)


def get_relationships(
    *, aif_path: str | None = None, files: list[str] | None = None, include_text_refs: bool = False
) -> dict:
    return query_service.get_relationships(_resolve_aif(aif_path), files, include_text_refs=include_text_refs)


get_relationships.__doc__ = TOOL_DESCRIPTIONS["get_relationships"]
mcp.tool()(get_relationships)


def get_graph_summary(
    *, aif_path: str | None = None, include_text_refs: bool = False, top_n: int = 10, code_only: bool = True
) -> dict:
    return query_service.get_graph_summary(
        _resolve_aif(aif_path), include_text_refs=include_text_refs, top_n=top_n, code_only=code_only
    )


get_graph_summary.__doc__ = TOOL_DESCRIPTIONS["get_graph_summary"]
mcp.tool()(get_graph_summary)


def get_dependents(*, aif_path: str | None = None, file: str, include_text_refs: bool = False) -> list[str]:
    return query_service.get_dependents(_resolve_aif(aif_path), file, include_text_refs=include_text_refs)


get_dependents.__doc__ = TOOL_DESCRIPTIONS["get_dependents"]
mcp.tool()(get_dependents)


def get_blast_radius(*, aif_path: str | None = None, file: str, include_text_refs: bool = False) -> list[str]:
    return query_service.get_blast_radius(_resolve_aif(aif_path), file, include_text_refs=include_text_refs)


get_blast_radius.__doc__ = TOOL_DESCRIPTIONS["get_blast_radius"]
mcp.tool()(get_blast_radius)


def get_detail(
    *,
    aif_path: str | None = None,
    file: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    return query_service.get_detail(_resolve_aif(aif_path), file, start_line, end_line)


get_detail.__doc__ = TOOL_DESCRIPTIONS["get_detail"]
mcp.tool()(get_detail)


def check_freshness(*, project_path: str | None = None, aif_path: str | None = None) -> dict:
    return query_service.check_freshness(_resolve_project_required(project_path), _resolve_aif(aif_path))


check_freshness.__doc__ = TOOL_DESCRIPTIONS["check_freshness"]
mcp.tool()(check_freshness)


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
mcp.tool()(search_project)


def main():
    parser = argparse.ArgumentParser(description="Ziplex MCP server")
    parser.add_argument("--version", action="version", version=f"ziplex-mcp {__version__}")
    parser.add_argument(
        "--aif",
        default=None,
        help="Default aif_path for any tool call that omits it (an explicit call-time aif_path still wins).",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Default project_path for any tool call that omits it (an explicit call-time project_path still wins).",
    )
    args = parser.parse_args()
    _defaults["aif"] = args.aif
    _defaults["project"] = args.project
    mcp.run()


if __name__ == "__main__":
    main()
