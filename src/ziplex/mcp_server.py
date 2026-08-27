"""Ziplex MCP server: exposes an already-packed project (`aif.json` plus its
sibling `<name>.detail.json`) as MCP tools, plus project-wide search.

Read-only by design -- this serves a human-curated pack (see edits.py /
corrector.py), it never re-packs or re-corrects a project on its own. That's
a deliberate choice, not a missing feature: Ziplex's identity is "a human
curates once, this serves that curated result," and letting an agent
silently trigger a fresh (uncorrected) pack would undercut the reason the
correction step exists. See the `ziplex-roadmap` memory for the full
benchmarking against repomix's MCP server this design is based on.

Every tool below is `query_service`'s matching function, registered as-is
(`mcp.tool()(query_service.get_overview)`, not a wrapper) -- nothing here has
logic or docstrings of its own. `query_service.py` is the same core
`gui_server.py`'s HTTP routes sit on top of; registering the function object
directly (rather than writing a `def get_overview(...): return
query_service.get_overview(...)` wrapper around it) keeps each tool's
docstring -- what the MCP SDK reads as its description -- living in exactly
one place, instead of needing a copy kept in sync here too.

Run directly (after `pip install -e .`):
    ziplex-mcp
    python -m ziplex.mcp_server
Add to Claude Code:
    claude mcp add ziplex -- ziplex-mcp
"""

from mcp.server import MCPServer

from . import query_service

mcp = MCPServer("ziplex")

for _fn in (
    query_service.get_overview,
    query_service.list_files,
    query_service.get_folders,
    query_service.get_relationships,
    query_service.get_dependents,
    query_service.get_blast_radius,
    query_service.get_detail,
    query_service.check_freshness,
    query_service.search_project,
):
    mcp.tool()(_fn)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
