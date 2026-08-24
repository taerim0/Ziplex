"""Querying an already-collected file set or an already-packed detail.json,
without knowing in advance which file has what you're after.

This is the piece Ziplex's "summary always loaded, detail fetched by
filename" design didn't have: an agent (or a human) that doesn't already
know which file to ask for has no way in. repomix's MCP server solves the
same problem with two sibling tools -- grep_repomix_output (regex + context
lines over the packed output) and read_repomix_output(startLine, endLine)
(partial reads of a large file) -- search_files() and read_detail_range()
below are the pure-function equivalents, built as CLI-reachable functions
now so a future MCP tool can wrap them directly instead of reinventing them.

search_files() searches original file content, not the `compressed` field a
pack() run would produce -- a match inside a function body that compression
stripped to a marker would otherwise be invisible. Compression trades
completeness for token count; search needs the completeness back.
"""

import re
from dataclasses import dataclass

from .file.textutil import read_text, relative_key


@dataclass(frozen=True)
class SearchMatch:
    file: str
    line_number: int  # 1-based
    line: str
    context_before: list[str]
    context_after: list[str]


def search_files(
    file_paths: list[str],
    root: str,
    pattern: str,
    context_lines: int = 0,
    ignore_case: bool = False,
) -> list[SearchMatch]:
    """Searches pattern (a regex) across file_paths, in file order then line
    order. file_paths is expected to already be a safe/selected set (e.g.
    from collect_files() + scan_files()) -- this doesn't filter or scan
    anything itself, and silently skips any file read_text() can't decode.

    Raises ValueError if pattern isn't a valid regex.
    """
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        raise ValueError(f"invalid regex: {e}") from e

    matches: list[SearchMatch] = []

    for file_path in file_paths:
        content = read_text(file_path)
        if content is None:
            continue

        lines = content.splitlines()
        for i, line in enumerate(lines):
            if not regex.search(line):
                continue
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            matches.append(SearchMatch(
                file=relative_key(file_path, root),
                line_number=i + 1,
                line=line,
                context_before=lines[start:i],
                context_after=lines[i + 1:end],
            ))

    return matches


def read_detail_range(compressed: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """1-based, inclusive line range over a compressed-body string (as stored
    per file in <name>.detail.json). None on either bound means "from the
    start" / "to the end". Out-of-range bounds are clamped rather than
    raising, matching Python slicing's own tolerance.

    Mirrors repomix's read_repomix_output(startLine, endLine) -- the piece
    Ziplex's per-file detail.json fetch doesn't have yet: today a caller gets
    the whole compressed body in one shot, wasteful once a file's compressed
    body is large and only a specific region is actually relevant.
    """
    lines = compressed.splitlines()
    start = max(0, (start_line - 1) if start_line else 0)
    end = min(len(lines), end_line if end_line else len(lines))
    return "\n".join(lines[start:end])
