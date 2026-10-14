"""The one place a file's dependency edges are produced: raw imports ->
project-level resolution (Go packages, TS aliases/workspaces, Python import
roots) -> text-reference mentions for non-code files -> merged list.

`pack()`, the `tree` CLI command and security-flagged graph-only files all go
through GraphBuilder, so a resolver change can't reach one caller and miss
another (that drift happened three times while each kept its own loop).
"""
from ..extract.code.extractor import extract_dependencies
from ..file.media import classify_media_file
from ..file.relationship import build_tree
from ..file.textutil import relative_key
from .import_context import expand_file_dependencies, resolve_import_context
from .text_references import TextRefIndex, find_text_references_for_file, merge_text_references


class GraphBuilder:
    """Per-run resolution context over a fixed file set (absolute paths)."""

    def __init__(self, root_path: str, file_paths: list[str]):
        self.root_path = root_path
        self.names = [relative_key(fp, root_path) for fp in file_paths]
        self.import_ctx = resolve_import_context(root_path, self.names)
        self.text_index = TextRefIndex(self.names)

    def code_dependencies(self, file_path: str, raw: list[str] | None = None) -> list[str]:
        """Resolved import dependencies; `raw` is the extractor's output when
        the caller already parsed the file."""
        if raw is None:
            raw = extract_dependencies(file_path)
        return expand_file_dependencies(file_path, relative_key(file_path, self.root_path), raw, self.import_ctx)

    def text_references(self, file_path: str) -> list[str]:
        """Collected files a non-code file mentions by path or unique basename
        (callers skip media files, which have no text)."""
        return find_text_references_for_file(
            file_path, relative_key(file_path, self.root_path), self.names, index=self.text_index,
        )

    def file_edges(self, file_path: str, raw: list[str] | None = None, scan_text: bool = True) -> tuple[list[str], list[str]]:
        """(dependencies, text_dependencies): code imports plus prose mentions
        merged, with the mention-only subset returned separately so
        build_tree() can tag those edges."""
        deps = self.code_dependencies(file_path, raw)
        refs = self.text_references(file_path) if scan_text and not classify_media_file(file_path) else []
        return merge_text_references(deps, refs)


def code_dependencies_of(data: dict) -> list[str]:
    """A files_data entry's import dependencies without its text mentions --
    what summary prompts and rule inference should see."""
    text = set(data.get("text_dependencies") or ())
    return [d for d in data.get("dependencies") or () if d not in text]


def build_graph(root_path: str, file_paths: list[str]) -> dict:
    """relationships-shaped graph ({name: {internal, external,
    internal_text_refs}}) for a file set, without summaries or packing."""
    builder = GraphBuilder(root_path, file_paths)
    files = {}
    for file_path in file_paths:
        deps, text_deps = builder.file_edges(file_path)
        files[relative_key(file_path, root_path)] = {"dependencies": deps, "text_dependencies": text_deps}
    return build_tree(files)
