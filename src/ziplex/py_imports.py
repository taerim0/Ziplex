"""Resolves Python imports to the concrete collected files they name,
before build_tree()'s generic stem matching ever sees them.

Two gaps this closes, both found packing fastapi/full-stack-fastapi-template
(28/79 Python import edges before):

- An absolute import (`from app.core.config import settings`) names a
  module relative to some sys.path root -- here `backend/`, not the project
  root. The stem heuristic matched it by its last segment only, so it could
  miss, or land on a same-named file in another language
  (`tests.utils.user` -> `frontend/tests/utils/user.ts`).
- `from app.api.routes import items, login` imports submodule *files*,
  while `from app.core.config import settings` imports a symbol. The
  extractor now emits each imported name as a `P.name` candidate; only the
  file system can tell which is which, so that is decided here.

Free (no LLM call) and conservative, like ts_paths.py: a dependency is
rewritten only when it lands on a collected file. The sys.path root isn't
known (no reliable way to read it from a project), so every ancestor folder
of the importing file is tried as one, nearest first.
"""

import posixpath
from functools import lru_cache

from .file.relationship import _resolve_python_relative

PY_SOURCE_EXTENSIONS = (".py", ".pyi")


def _module_file(path: str, all_names: set) -> str | None:
    for candidate in (path + ".py", path + ".pyi", posixpath.join(path, "__init__.py")):
        if candidate in all_names:
            return candidate
    return None


@lru_cache(maxsize=4)
def _module_suffix_index(all_names: frozenset) -> dict[str, list[str]]:
    """Every trailing module path of every collected Python file ->
    the files it could name: `src/ziplex/file/relationship.py` is reachable
    as `relationship`, `file/relationship`, `ziplex/file/relationship`, ...;
    a package's `__init__.py` under its folder path."""
    index: dict[str, list[str]] = {}
    for name in all_names:
        if not name.endswith(PY_SOURCE_EXTENSIONS):
            continue
        module = name.rsplit(".", 1)[0]
        if module.endswith("/__init__") or module == "__init__":
            module = posixpath.dirname(module)
        parts = module.split("/")
        for i in range(len(parts)):
            index.setdefault("/".join(parts[i:]), []).append(name)
    return index


def resolve_python_module(dep: str, source_name: str, all_names) -> str | None:
    """The collected file a dotted module path names, or None. Ancestors of
    the importing file are tried as sys.path roots first; failing that, a
    unique collected file whose path ends with the module path wins -- the
    src-layout case (`tests/` importing `ziplex.x` from `src/ziplex/x.py`,
    where `src/` is no ancestor of `tests/`). Ambiguous -> None."""
    if dep.startswith("."):
        return _resolve_python_relative(dep, source_name, all_names)
    rel = dep.replace(".", "/")
    folder = posixpath.dirname(source_name)
    while True:
        hit = _module_file(posixpath.join(folder, rel) if folder else rel, all_names)
        if hit:
            return hit
        if not folder:
            break
        folder = posixpath.dirname(folder)
    matches = _module_suffix_index(frozenset(all_names)).get(rel, [])
    return matches[0] if len(matches) == 1 else None


def _parent(dep: str) -> str | None:
    """`app.core.config.settings` -> `app.core.config`; `.paths.X` ->
    `.paths`; None when there's no name after the module part."""
    stripped = dep.lstrip(".")
    if "." not in stripped:
        return None
    return dep.rsplit(".", 1)[0]


def rewrite_python_imports(name: str, deps: list[str], all_names: set) -> list[str]:
    """deps with each import that lands on a collected file replaced by that
    file's name. A `P.name` candidate that doesn't resolve is a symbol, not
    a module, and is dropped (only when `P` itself is also in deps, i.e. it
    came from `from P import name`). A package's own `__init__.py` edge is
    dropped when a submodule of it resolved from the same statement -- the
    import names that submodule, not the package. Anything else unresolved
    passes through untouched."""
    if not name.endswith(PY_SOURCE_EXTENSIONS):
        return deps
    present = set(deps)
    resolved = {d: resolve_python_module(d, name, all_names) for d in present}
    covered_packages = {
        parent for d, hit in resolved.items()
        if hit and (parent := _parent(d)) in present and (resolved.get(parent) or "").endswith("__init__.py")
    }
    out = []
    for dep in deps:
        hit = resolved[dep]
        if hit:
            if dep not in covered_packages:
                out.append(hit)
        elif _parent(dep) not in present:
            out.append(dep)
    return out
