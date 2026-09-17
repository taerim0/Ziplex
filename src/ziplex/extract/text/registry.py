"""Registers compression functions, by extension, for text files with no AST (JSON, etc).

Mirrors how extract/code/languages.py collects per-language Tree-sitter config in
one place — here it's just extension -> compress function. To support a new text
format, write a compress function in this package and register it in
TEXT_COMPRESSORS.
"""

from typing import Callable

from .dockerfile import compress_dockerfile
from .json import compress_json
from .markdown import compress_markdown
from .txt import compress_txt
from .yaml import compress_yaml

TEXT_COMPRESSORS: dict[str, Callable[[str], str]] = {
    ".dockerfile": compress_dockerfile,
    ".json": compress_json,
    ".md": compress_markdown,
    ".txt": compress_txt,
    ".yaml": compress_yaml,
    ".yml": compress_yaml,
    # MapleStory Worlds (Maker) project files -- plain JSON under a
    # platform-specific extension, confirmed directly against a real
    # packed project (testfiles/Practice/): every .model/.codeblock/
    # .directory/.userdataset/.tileset sampled parses as JSON with the
    # same {Id, GameId, EntryKey, ContentType, Content, ...} envelope.
    # Before this, none of the five matched any LanguageConfig or text
    # compressor, so compress_file() passed 152/209 of that project's
    # files through completely unstructured -- zero signatures/deps
    # extracted, and confidence.py's "no signatures -> trust it" rule
    # then gave every one of them a false 1.0, flagging nothing for
    # review despite there being no real structural signal at all.
    # compress_json() already falls back to the raw, unchanged text on a
    # json.loads() failure, so mapping these here is safe even for an
    # unrelated project that happens to reuse one of these extensions for
    # something else (e.g. a KDE .directory file, which is INI, not
    # JSON) -- worst case is just no compression benefit for that file,
    # never corrupted output.
    ".model": compress_json,
    ".codeblock": compress_json,
    ".directory": compress_json,
    ".userdataset": compress_json,
    ".tileset": compress_json,
}


def get_text_compressor(ext: str) -> Callable[[str], str] | None:
    return TEXT_COMPRESSORS.get(ext)
