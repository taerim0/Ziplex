from pathlib import Path
from tree_sitter import Parser

from .languages import get_language_config


def get_parser_by_ext(ext: str) -> Parser | None:
    config = get_language_config(ext)
    if not config:
        return None
    return Parser(config.language)


def get_parser(file_path: str) -> Parser | None:
    return get_parser_by_ext(Path(file_path).suffix)
