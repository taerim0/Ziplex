"""Registers compression functions, by extension, for text files with no AST (JSON, etc).

Mirrors how extract/code/languages.py collects per-language Tree-sitter config in
one place — here it's just extension -> compress function. To support a new text
format, write a compress function in this package and register it in
TEXT_COMPRESSORS.
"""

from typing import Callable

from .json import compress_json
from .markdown import compress_markdown
from .txt import compress_txt
from .yaml import compress_yaml

TEXT_COMPRESSORS: dict[str, Callable[[str], str]] = {
    ".json": compress_json,
    ".md": compress_markdown,
    ".txt": compress_txt,
    ".yaml": compress_yaml,
    ".yml": compress_yaml,
}


def get_text_compressor(ext: str) -> Callable[[str], str] | None:
    return TEXT_COMPRESSORS.get(ext)
