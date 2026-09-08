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

from .file.textutil import parent_folder, read_text as _read_text


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
            index.setdefault(parent_folder(name), []).append(name)
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

    Not meant to be called directly by pack()/`tree` any more -- see
    resolve_go_context()/expand_dependencies_for_file() below, which wrap
    this and the two once-per-run calls it needs so a real caller has one
    pair of functions to call instead of reimplementing the setup+gate
    around this one. Kept public (and still exercised directly in tests)
    since it's the actual expansion algorithm; the wrappers are just the
    call-site glue that used to be duplicated.
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


def resolve_go_context(root_path: str, all_names: list[str]) -> tuple[str | None, dict[str, list[str]]]:
    """Bundles read_go_module_path()/build_go_package_index() -- the two
    once-per-run calls every expand_dependencies_for_file() caller needs
    before it can call that function at all. packager.py's pack() and
    cli.py's `tree` subcommand used to each inline this exact pair
    separately (same two lines, copy-pasted); a future third caller (or a
    change to how either step works) now only needs this one function
    updated, not every call site re-audited by hand -- see
    expand_go_dependencies()'s own docstring for why that kind of
    per-call-site drift has already bitten this feature once before, for a
    different piece of it (text_references.py's merge step).
    """
    go_module_path = read_go_module_path(root_path)
    go_package_index = build_go_package_index(all_names) if go_module_path else {}
    return go_module_path, go_package_index


def expand_dependencies_for_file(
    file_path: str, name: str, deps: list[str],
    go_module_path: str | None, go_package_index: dict[str, list[str]],
) -> list[str]:
    """The per-file gate expand_go_dependencies() itself doesn't apply --
    only a .go file, and only when go_module_path was actually resolved
    (resolve_go_context() above), is ever worth expanding at all. Wraps the
    exact `if go_module_path and file_path.endswith(".go")` check both real
    callers used to repeat inline, so a future third caller can't
    accidentally spell that condition slightly differently, or forget it
    outright the way the now-removed `analyze` subcommand once did (caught
    only by the next code review -- see expand_go_dependencies()'s own
    docstring). deps passes through unchanged for every other file.
    """
    if go_module_path and file_path.endswith(".go"):
        return expand_go_dependencies(deps, name, go_module_path, go_package_index)
    return deps
