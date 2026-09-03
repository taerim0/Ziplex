"""Ziplex MCP server: exposes an already-packed project (`aif.json` plus its
sibling `<name>.detail.json`) as MCP tools, plus project-wide search.

Read-only by design -- this serves a human-curated pack (see edits.py /
corrector.py), it never re-packs or re-corrects a project on its own. That's
a deliberate choice, not a missing feature: Ziplex's identity is "a human
curates once, this serves that curated result," and letting an agent
silently trigger a fresh (uncorrected) pack would undercut the reason the
correction step exists. See the `ziplex-roadmap` memory for the full
benchmarking against repomix's MCP server this design is based on.

Every tool below wraps `query_service`'s matching function -- the wrapper
exists only to make `aif_path`/`project_path` optional, defaulting to
whatever `--aif`/`--project` this server was started with (see main()),
since a real session is almost always one Claude Code project talking to
one already-packed project, and re-typing the same absolute path on every
single call is both friction and a real place for a typo to silently point
at the wrong file. Each wrapper's docstring is assigned from the matching
query_service function's own `__doc__` (`wrapper.__doc__ = query_service.
fn.__doc__`, set *before* `mcp.tool()` registers it -- the decorator reads
`__doc__` at registration time, so setting it after would be too late)
rather than retyped, so the actual text -- what the MCP SDK reads as the
tool description -- still only lives in query_service.py; nothing here is a
second copy that could drift out of sync with it. Every other parameter
(file, folder, pattern, ...) passes straight through untouched.

Run directly (after `pip install -e .`):
    ziplex-mcp
    ziplex-mcp --aif result/Ziplex.json --project .
    python -m ziplex.mcp_server
Add to Claude Code -- typically one registration per project, so the paths
are baked into the registration itself rather than re-typed on every call:
    claude mcp add ziplex -- ziplex-mcp --aif result/Ziplex.json --project .
"""

import argparse

from mcp.server import MCPServer

from . import query_service
from . import __version__

mcp = MCPServer("ziplex")

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


def get_overview(*, aif_path: str | None = None, project_path: str | None = None) -> dict:
    return query_service.get_overview(_resolve_aif(aif_path), _resolve_project_optional(project_path))


get_overview.__doc__ = query_service.get_overview.__doc__
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


list_files.__doc__ = query_service.list_files.__doc__
mcp.tool()(list_files)


def get_folders(*, aif_path: str | None = None) -> dict:
    return query_service.get_folders(_resolve_aif(aif_path))


get_folders.__doc__ = query_service.get_folders.__doc__
mcp.tool()(get_folders)


def get_relationships(*, aif_path: str | None = None, files: list[str] | None = None) -> dict:
    return query_service.get_relationships(_resolve_aif(aif_path), files)


get_relationships.__doc__ = query_service.get_relationships.__doc__
mcp.tool()(get_relationships)


def get_dependents(*, aif_path: str | None = None, file: str, include_text_refs: bool = True) -> list[str]:
    return query_service.get_dependents(_resolve_aif(aif_path), file, include_text_refs=include_text_refs)


get_dependents.__doc__ = query_service.get_dependents.__doc__
mcp.tool()(get_dependents)


def get_blast_radius(*, aif_path: str | None = None, file: str, include_text_refs: bool = True) -> list[str]:
    return query_service.get_blast_radius(_resolve_aif(aif_path), file, include_text_refs=include_text_refs)


get_blast_radius.__doc__ = query_service.get_blast_radius.__doc__
mcp.tool()(get_blast_radius)


def get_detail(
    *,
    aif_path: str | None = None,
    file: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    return query_service.get_detail(_resolve_aif(aif_path), file, start_line, end_line)


get_detail.__doc__ = query_service.get_detail.__doc__
mcp.tool()(get_detail)


def check_freshness(*, project_path: str | None = None, aif_path: str | None = None) -> dict:
    return query_service.check_freshness(_resolve_project_required(project_path), _resolve_aif(aif_path))


check_freshness.__doc__ = query_service.check_freshness.__doc__
mcp.tool()(check_freshness)


def search_project(
    *,
    project_path: str | None = None,
    pattern: str,
    context_lines: int = 0,
    ignore_case: bool = False,
    max_results: int | None = query_service.DEFAULT_SEARCH_MAX_RESULTS,
) -> dict:
    return query_service.search_project(
        _resolve_project_required(project_path), pattern, context_lines, ignore_case, max_results
    )


search_project.__doc__ = query_service.search_project.__doc__
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
