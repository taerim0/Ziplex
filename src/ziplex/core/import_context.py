"""The one pair of calls every per-file dependency loop makes before
build_tree(): resolve_import_context() once per run, then
expand_file_dependencies() per file. Bundles each language's
project-level import resolution (Go packages via go.mod, TS/JS path
aliases and workspace packages, Python modules against their sys.path
root) so pack() and the `tree` subcommand can't drift
apart by wiring a new language into one and forgetting the other -- the
exact failure the text-reference merge and Go expansion each hit once.
"""

from dataclasses import dataclass

from .go_packages import resolve_go_context, expand_dependencies_for_file as _expand_go
from .py_imports import rewrite_python_imports
from .ts_paths import TsPathIndex, resolve_ts_context


@dataclass(frozen=True)
class ImportContext:
    go_module_path: str | None
    go_package_index: dict
    ts_index: TsPathIndex | None
    all_names: frozenset = frozenset()


def resolve_import_context(root_path: str, all_names: list[str]) -> ImportContext:
    go_module_path, go_package_index = resolve_go_context(root_path, all_names)
    return ImportContext(
        go_module_path, go_package_index, resolve_ts_context(root_path, all_names), frozenset(all_names)
    )


def expand_file_dependencies(file_path: str, name: str, deps: list[str], ctx: ImportContext) -> list[str]:
    deps = _expand_go(file_path, name, deps, ctx.go_module_path, ctx.go_package_index)
    if ctx.ts_index is not None:
        deps = ctx.ts_index.rewrite(name, deps)
    return rewrite_python_imports(name, deps, ctx.all_names)
