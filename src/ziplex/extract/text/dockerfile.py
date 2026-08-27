"""Structure-preserving compression for Dockerfiles.

Dockerfiles are flat and declarative -- one instruction per top-level
statement, never nested, with no "function body" concept the way
Ziplex's Tree-sitter code compressors need. This fits the same "keep
structure, elide only what's long" model json.py/yaml.py already use
for their own declarative formats, not extract/code's body-stripping
model -- so it lives here, not as a LanguageConfig entry in
extract/code/languages.py, even though it's implemented on top of a
real Tree-sitter grammar.

Sourced from tree_sitter_language_pack's bundled copy, the same
mechanism GDScript's own LanguageConfig already uses -- not a dedicated
tree_sitter_dockerfile import the way most other languages get theirs.
Confirmed directly before choosing this, not assumed: the dedicated
`tree-sitter-dockerfile` PyPI package's only real (non-stub) release as
of this writing ships macOS/Linux wheels ONLY -- the sole
Windows-compatible artifact is an ancient 0.0.0a1 stub with no working
language() binding at all, which pip installs silently instead of
failing loudly. The bundled copy sidesteps the whole problem.
"""

from tree_sitter import Parser
from tree_sitter_language_pack import get_language

# A run of consecutive lines belonging to one logical instruction (a
# RUN's shell command chained across several `\`-continued physical
# lines is the real-world case this exists for -- an `apt-get install`
# package list is often the single largest source of token bloat in a
# real Dockerfile) keeps only the leading lines past this count, eliding
# the rest with a marker -- same idea as txt.py's MAX_BLOCK_LINES,
# applied to one instruction's own line range instead of an arbitrary
# run of lines with no structural meaning.
MAX_INSTRUCTION_LINES = 5
# Any single physical line (whether or not it belongs to a truncated
# instruction) longer than this gets truncated too -- same threshold as
# every other text compressor here, for consistency.
MAX_LINE_LEN = 200

MARKER = "⋮----"

_LANGUAGE = None


def _dockerfile_language():
    # Lazily resolved and cached at module scope -- get_language() does a
    # real (if fast) lookup into the bundled grammar set, not free, and
    # every other text compressor here has zero per-call setup cost; a
    # whole-project pack compressing several Dockerfiles would otherwise
    # pay that lookup on every single call for no reason.
    global _LANGUAGE
    if _LANGUAGE is None:
        _LANGUAGE = get_language("dockerfile")
    return _LANGUAGE


def compress_dockerfile(text: str) -> str:
    """Compresses a Dockerfile by keeping each instruction's own line(s)
    up to MAX_INSTRUCTION_LINES, eliding a longer instruction's remaining
    lines with a marker, and truncating any individually long line the
    same way every other text compressor here does.

    Only Tree-sitter's instruction *boundaries* are used (each top-level
    `*_instruction` node's own line range) -- not any deeper per-field
    structure -- specifically so a RUN's shell command spanning several
    `\\`-continued physical lines is judged and truncated as the one
    logical unit it actually is, not blindly line-by-line the way
    applying txt.py's own generic block-length check directly to the raw
    file text would (which has no way to know where one instruction
    actually ends and the next begins).

    Falls back to the original text unchanged on any parse failure --
    the same "don't guess, don't corrupt" contract every other text
    compressor here already has for unparseable input.
    """
    try:
        parser = Parser(_dockerfile_language())
        tree = parser.parse(text.encode("utf-8"))
    except Exception:
        return text

    instruction_ranges = [
        (child.start_point[0], child.end_point[0])
        for child in tree.root_node.children
        if child.type.endswith("_instruction")
    ]

    lines = text.splitlines()
    result: list[str] = []
    ranges = iter(instruction_ranges)
    current = next(ranges, None)
    i = 0
    n = len(lines)

    while i < n:
        if current is not None and i == current[0]:
            start, end = current
            block = lines[start:end + 1]
            if len(block) > MAX_INSTRUCTION_LINES:
                result.extend(_truncate_line(ln) for ln in block[:MAX_INSTRUCTION_LINES])
                remaining = len(block) - MAX_INSTRUCTION_LINES
                result.append(f"    {MARKER} ({remaining}줄 생략)")
            else:
                result.extend(_truncate_line(ln) for ln in block)
            i = end + 1
            current = next(ranges, None)
        else:
            result.append(_truncate_line(lines[i]))
            i += 1

    return "\n".join(result)


def _truncate_line(line: str) -> str:
    if len(line) > MAX_LINE_LEN:
        return line[:MAX_LINE_LEN] + f" {MARKER} (생략)"
    return line
