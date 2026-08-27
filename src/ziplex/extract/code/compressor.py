from .languages import get_language_config
from .parser import get_parser_by_ext
from ...file.textutil import read_text
from pathlib import Path

MARKER = "    ⋮----"

def _effective_ext(file_path: str) -> str:
    """Path(file_path).suffix normally -- except a Dockerfile, which by
    convention is almost always named with NO extension at all (a bare
    "Dockerfile", or a "Dockerfile.dev"/"Dockerfile.prod" stage-suffixed
    variant), not something Ziplex's purely suffix-based dispatch could
    ever match on its own. Recognized by filename instead (case-
    insensitive): exactly "dockerfile", a "<something>.dockerfile" shape
    (already resolves to this same key via Path.suffix alone -- kept
    here only for the docstring's sake, not a separate code path), or a
    "dockerfile.<something>" shape -- mapped to the fixed ".dockerfile"
    key so it fits every other registry's existing dot-prefixed key
    format rather than becoming a special case downstream.

    A real, non-hypothetical trap the "dockerfile.<something>" branch
    has to avoid: a source file that's genuinely named e.g. "Dockerfile
    .py" or "dockerfile.js" (unusual, but real -- a helper script for
    generating a Dockerfile is a plausible name for exactly that) is
    NOT a stage-suffixed Dockerfile variant at all, and misrouting it to
    the Dockerfile text compressor would silently lose that file's real
    language compression/signature extraction (caught by code review,
    not written defensively up front). Resolved by checking the part
    after the first "." against the *actual* registries a real
    extension would resolve through -- a recognized language/text
    extension always wins over the Dockerfile guess, since a stage
    suffix like "dev"/"prod"/"ci"/"arm64" is deliberately never going to
    collide with a real code/text extension anyway.
    """
    name = Path(file_path).name.lower()
    if name == "dockerfile" or name.endswith(".dockerfile"):
        return ".dockerfile"
    if name.startswith("dockerfile."):
        suffix = Path(file_path).suffix
        # Deferred import: see compress_file()'s own comment for why --
        # this breaks the same extract.code <-> extract.text import cycle.
        from ..text.registry import get_text_compressor
        if get_language_config(suffix) or get_text_compressor(suffix):
            return suffix
        return ".dockerfile"
    return Path(file_path).suffix


def compress_file(file_path: str) -> str:
    code = read_text(file_path)
    if code is None:
        # binary or otherwise unreadable as text -> not something to compress
        return ""

    ext = _effective_ext(file_path)
    compressed = compress_code(code, ext)
    if compressed is not None:
        return compressed

    # No Tree-sitter grammar for this extension -> use a text-only compressor if
    # one is registered, else pass the file through unchanged.
    # (Imported here, not at module top: extract.text.registry -> extract.text.markdown
    # calls back into this module's compress_code() for code-block compression, so a
    # top-level import in both directions would create a circular import. A deferred
    # import breaks the cycle since both modules are already fully loaded by then.)
    from ..text.registry import get_text_compressor
    text_compressor = get_text_compressor(ext)
    return text_compressor(code) if text_compressor else code


def compress_code(code: str, ext: str) -> str | None:
    """Compresses code text with the Tree-sitter grammar matching ext.

    Returns None if ext isn't a supported language, so callers can fall back to
    something else. compress_file() and Markdown code-block compression
    (extract.text.markdown) share this logic — whether it's a real file or a code
    block inside a Markdown doc, "how to compress this language" should be the
    same either way.
    """
    parser = get_parser_by_ext(ext)
    if not parser:
        return None

    config = get_language_config(ext)
    function_types = config.function_types if config else []

    tree = parser.parse(bytes(code, "utf8"))
    lines = code.splitlines()

    # collect the line ranges covered by function bodies
    body_ranges = []
    _collect_bodies(tree.root_node, body_ranges, function_types)

    # strip bodies line by line
    result = []
    i = 0
    while i < len(lines):
        removed = False
        for start, end in body_ranges:
            if i == start:
                result.append(MARKER)
                i = end + 1
                removed = True
                break
        if not removed:
            result.append(lines[i])
            i += 1

    return "\n".join(result)


def _collect_bodies(node, ranges: list, function_types: list):

    if node.type in function_types:
        body = node.child_by_field_name("body")
        if body:
            start = body.start_point[0]
            end   = body.end_point[0]

            # In brace languages (JS/TS/Java) the opening '{' sits on the same line
            # as the signature, so blanking the body's own start line would erase
            # the signature too. Only skip that line if the body actually starts on
            # the same line as the function node itself. Python's body (an indented
            # block) already starts on the next line, so this never triggers there.
            if start == node.start_point[0]:
                start += 1

            # Likewise, keep a line that's just the closing '}'. Brace-less
            # languages (Python) have no such token as the body's last child, so
            # this is a no-op for them.
            last_child = body.children[-1] if body.children else None
            if last_child and last_child.type == "}":
                end = last_child.start_point[0] - 1

            if end >= start:
                ranges.append((start, end))
        return  # don't recurse into the body

    for child in node.children:
        _collect_bodies(child, ranges, function_types)
