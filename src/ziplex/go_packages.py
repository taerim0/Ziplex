"""Resolves Go's package-shaped internal imports to the concrete files that
make up the imported package -- closing the gap `_go_dependency_handler`'s
own docstring (`extract/code/languages.py`) used to document as a permanent
limitation.

Unlike every other supported language, a Go import path
(`"myproject/internal/utils"`) names a *package* -- typically a whole
directory of files sharing one namespace -- not a single file.
`extract_dependencies()` still only ever returns that raw string; without
this module, `resolve_dependency()`'s file-stem matching has nothing to
match it against (the string names a directory, not a file), so an internal
multi-package import silently resolved as "external" for good, regardless
of how much of the rest of the project a change to that package would
actually reach.

Deliberately free (no LLM call) and precise, the same design text_references.
py already uses for a related problem: read go.mod's own `module` directive
to learn the import-path prefix every one of *this* project's own packages
shares, strip that prefix off an import to get the internal package's
directory, and match that directory against the *actual* collected .go file
list -- never a generic "looks like an internal path" heuristic. A project
with no go.mod (or no `module` line in it) gets read_go_module_path() ->
None, at which point expand_go_dependencies() is never worth calling at all
-- every caller below gates on that.
"""

from pathlib import Path

from .file.textutil import read_text as _read_text


def read_go_module_path(root_path: str) -> str | None:
    """Reads the module path off go.mod's `module <path>` directive -- the
    import-path prefix every one of this project's own packages shares, and
    the only way to tell an internal multi-file package import
    (`"<module path>/internal/utils"`) apart from a genuinely external one
    (`"github.com/someone-else/lib"`) without it. Root-level only, the same
    scope limit tech_stack.py's own manifest scan takes -- a monorepo
    submodule with its own go.mod isn't chased.

    None for no go.mod, an unreadable one, or one with no `module` line at
    all -- any of which makes internal-package resolution impossible, not
    an error worth surfacing (see this module's own docstring).
    """
    text = _read_text(str(Path(root_path) / "go.mod"))
    if text is None:
        return None
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if line.startswith("module "):
            return line[len("module "):].strip()
    return None


def build_go_package_index(all_names: list[str]) -> dict[str, list[str]]:
    """Groups every .go file among all_names by its own directory (POSIX,
    "." for a root-level file -- the same convention folder_summary.py
    uses) -- the file-level counterpart to a Go import path once
    read_go_module_path()'s own prefix has been stripped off it. Cheap and
    always safe to call, even for a project with zero .go files (an empty
    dict).
    """
    index: dict[str, list[str]] = {}
    for name in all_names:
        if name.endswith(".go"):
            index.setdefault(Path(name).parent.as_posix(), []).append(name)
    return index


def _internal_package_dir(dep: str, module_path: str) -> str | None:
    """The internal package directory dep's import path names, or None if
    dep isn't rooted under module_path at all (the Go standard library, or
    a genuinely external module) -- left completely alone in that case.
    "." for the module's own root package, matching build_go_package_index()
    's own convention for a root-level file's directory.
    """
    if dep == module_path:
        return "."
    prefix = module_path + "/"
    return dep[len(prefix):] if dep.startswith(prefix) else None


def expand_go_dependencies(
    deps: list[str], self_name: str, module_path: str, index: dict[str, list[str]]
) -> list[str]:
    """The one place that turns a Go package import into the concrete file
    entries resolve_dependency() can already match exactly (see its first,
    exact-collected-filename check) -- every caller merging
    extract_dependencies()'s raw Go import strings into a file's
    `dependencies` must run the result through this, or an internal
    multi-file Go import keeps silently resolving as external.

    A dep not rooted under module_path (stdlib, a genuinely external
    module) passes through untouched. One rooted under module_path but
    matching no actual collected .go file (a package that exists on disk
    but wasn't selected/collected, or a genuine typo) also passes through
    untouched rather than vanishing -- it still shows up as an unresolved,
    external-looking dependency instead of disappearing silently. A target
    package importing itself back (illegal Go, never happens in practice)
    can't produce a self-edge either way: self_name is always excluded from
    the expansion, and build_tree() drops a literal self-reference besides.

    packager.py's per-file loop and cli.py's `tree`/`analyze` subcommands
    are today's only three callers of extract_dependencies() on a .go file
    -- all three must call this the same way, or they diverge on the same
    feature's output the way text_references.py's equivalent merge step
    already did once between two of these same call sites (see that
    module's own docstring), and the way `analyze` itself was actually
    missed on this feature's first pass, caught only by the very next code
    review. Check every real caller again before assuming this is done.
    """
    expanded = []
    for dep in deps:
        pkg_dir = _internal_package_dir(dep, module_path)
        if pkg_dir is None:
            expanded.append(dep)
            continue
        targets = [f for f in index.get(pkg_dir, []) if f != self_name]
        expanded.extend(targets if targets else [dep])
    return expanded
