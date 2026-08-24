import re

# Lists: a run of consecutive items longer than this keeps only the first few,
# eliding the rest with a marker.
MAX_LIST_ITEMS = 5
# Paragraphs: text longer than this gets truncated with a marker appended.
# (same threshold as json's MAX_STRING_LEN, for consistency)
MAX_PARAGRAPH_LEN = 200
# Code blocks: a body longer than this many lines keeps only the first few,
# eliding the rest with a marker.
MAX_CODEBLOCK_LINES = 10
# Tables: data rows (header/separator excluded) beyond this count keep only
# the first few.
MAX_TABLE_ROWS = 5

MARKER = "⋮----"

_LIST_ITEM_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")

# Code-block language tag (```python etc.) -> the extension extract/code knows.
# Kept in sync with languages.py's LANGUAGE_CONFIGS keys (.py/.java/.ts/.js) — if a
# language is added there, add its alias(es) here too so its code blocks get
# structural compression.
_FENCE_LANG_TO_EXT = {
    "python": ".py",
    "py": ".py",
    "java": ".java",
    "typescript": ".ts",
    "ts": ".ts",
    "javascript": ".js",
    "js": ".js",
    "jsx": ".js",
    "tsx": ".ts",
    "lua": ".lua",
    "gdscript": ".gd",
    "gd": ".gd",
}


def compress_markdown(text: str) -> str:
    """Compresses Markdown by keeping structure (headings) and eliding repetitive/long content.

    Applies the same idea as code compression — keep the function signature, blank
    only the body — to line-oriented Markdown:
    - headings (#...) and blank lines are always kept as-is (they're the document's
      structure)
    - a run of list items longer than MAX_LIST_ITEMS keeps only the leading items,
      eliding the rest with a marker
    - code blocks: if the fence has a language tag extract/code knows (.py/.java/
      .ts/.js), compress_code() is applied first for "real" structural compression
      — signatures survive, only bodies get elided. If the result still exceeds
      MAX_CODEBLOCK_LINES (e.g. a plain script with no functions), it's truncated
      to the first few lines plus a marker. Untagged/unsupported languages go
      straight to the line-count truncation. Opening/closing fences are always kept.
    - table data rows beyond MAX_TABLE_ROWS keep only the leading rows, eliding the
      rest with a marker (the header row + separator are always kept)
    - a plain paragraph longer than MAX_PARAGRAPH_LEN gets truncated with a marker
      appended

    There's no Tree-sitter grammar for Markdown, so this isn't real AST parsing —
    it's a regex-based, line-oriented approximation. Unlike json there's no
    "parsing failed -> return the original" case to handle, since Markdown is
    always "valid"; compression is always attempted.
    """
    lines = text.splitlines()
    result: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if _FENCE_RE.match(line):
            i = _compress_codeblock(lines, i, result)
            continue

        if _LIST_ITEM_RE.match(line):
            i = _compress_list(lines, i, result)
            continue

        if _TABLE_ROW_RE.match(line):
            i = _compress_table(lines, i, result)
            continue

        if line.strip() == "" or _HEADING_RE.match(line):
            result.append(line)
            i += 1
            continue

        i = _compress_paragraph(lines, i, result)

    return "\n".join(result)


def _compress_codeblock(lines: list[str], i: int, result: list[str]) -> int:
    fence_line = lines[i]
    fence = _FENCE_RE.match(fence_line).group(1)
    lang = fence_line.strip().removeprefix(fence).strip().lower()
    result.append(fence_line)
    i += 1

    body_start = i
    n = len(lines)
    while i < n and not lines[i].strip().startswith(fence):
        i += 1
    body = lines[body_start:i]

    body = _compress_codeblock_body(body, lang)

    if len(body) > MAX_CODEBLOCK_LINES:
        result.extend(body[:MAX_CODEBLOCK_LINES])
        remaining = len(body) - MAX_CODEBLOCK_LINES
        result.append(f"{MARKER} ({remaining}줄 생략)")
    else:
        result.extend(body)

    if i < n:  # closing fence
        result.append(lines[i])
        i += 1

    return i


def _compress_codeblock_body(body: list[str], lang: str) -> list[str]:
    ext = _FENCE_LANG_TO_EXT.get(lang)
    if not ext:
        return body

    # Deferred import: see the comment next to compress_file() in compressor.py for
    # why. This breaks the cycle extract.code.compressor -> extract.text.registry ->
    # extract.text.markdown -> extract.code.compressor by importing inside the function.
    from ..code.compressor import compress_code

    compressed = compress_code("\n".join(body), ext)
    return body if compressed is None else compressed.splitlines()


def _compress_list(lines: list[str], i: int, result: list[str]) -> int:
    items = []
    n = len(lines)
    while i < n and _LIST_ITEM_RE.match(lines[i]):
        items.append(lines[i])
        i += 1

    if len(items) > MAX_LIST_ITEMS:
        result.extend(items[:MAX_LIST_ITEMS])
        remaining = len(items) - MAX_LIST_ITEMS
        result.append(f"{MARKER} ({remaining}개 항목 생략)")
    else:
        result.extend(items)

    return i


def _compress_table(lines: list[str], i: int, result: list[str]) -> int:
    rows = []
    n = len(lines)
    while i < n and _TABLE_ROW_RE.match(lines[i]):
        rows.append(lines[i])
        i += 1

    # header row + separator (| --- | --- |) are always kept; only cap the data rows after them
    header = rows[:2]
    data_rows = rows[2:]
    result.extend(header)

    if len(data_rows) > MAX_TABLE_ROWS:
        result.extend(data_rows[:MAX_TABLE_ROWS])
        remaining = len(data_rows) - MAX_TABLE_ROWS
        result.append(f"{MARKER} ({remaining}개 행 생략)")
    else:
        result.extend(data_rows)

    return i


def _compress_paragraph(lines: list[str], i: int, result: list[str]) -> int:
    n = len(lines)
    para_start = i
    while (
        i < n
        and lines[i].strip() != ""
        and not _LIST_ITEM_RE.match(lines[i])
        and not _TABLE_ROW_RE.match(lines[i])
        and not _FENCE_RE.match(lines[i])
        and not _HEADING_RE.match(lines[i])
    ):
        i += 1

    para_lines = lines[para_start:i]
    para_text = "\n".join(para_lines)

    if len(para_text) > MAX_PARAGRAPH_LEN:
        result.append(para_text[:MAX_PARAGRAPH_LEN] + f" {MARKER} (생략)")
    else:
        result.extend(para_lines)

    return i
