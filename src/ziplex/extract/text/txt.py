# Lines: a run of consecutive non-blank lines longer than this keeps only the
# leading lines, eliding the rest with a marker (e.g. long logs/dumps).
MAX_BLOCK_LINES = 10
# A single line longer than this gets truncated with a marker appended.
# (same threshold as json's MAX_STRING_LEN / markdown's MAX_PARAGRAPH_LEN,
# for consistency)
MAX_LINE_LEN = 200

MARKER = "⋮----"


def compress_txt(text: str) -> str:
    """Compresses plain text by eliding long, repetitive content.

    Plain .txt has no structure to preserve the way Markdown (headings/lists/code
    fences) or JSON (keys) do, so this just applies the same two generic rules the
    other text compressors use for their "body" content:
    - a run of consecutive non-blank lines longer than MAX_BLOCK_LINES keeps only
      the leading lines, eliding the rest with a marker
    - any line longer than MAX_LINE_LEN is truncated with a marker appended
    Blank lines are always kept as-is, since they're the only structure plain text
    has (paragraph breaks).
    """
    lines = text.splitlines()
    result: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if line.strip() == "":
            result.append(line)
            i += 1
            continue

        i = _compress_block(lines, i, result)

    return "\n".join(result)


def _compress_block(lines: list[str], i: int, result: list[str]) -> int:
    n = len(lines)
    block: list[str] = []
    while i < n and lines[i].strip() != "":
        block.append(_truncate_line(lines[i]))
        i += 1

    if len(block) > MAX_BLOCK_LINES:
        result.extend(block[:MAX_BLOCK_LINES])
        remaining = len(block) - MAX_BLOCK_LINES
        result.append(f"{MARKER} ({remaining}줄 생략)")
    else:
        result.extend(block)

    return i


def _truncate_line(line: str) -> str:
    if len(line) > MAX_LINE_LEN:
        return line[:MAX_LINE_LEN] + f" {MARKER} (생략)"
    return line
