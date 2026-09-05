from pathlib import Path


def build_stem_map(file_names) -> dict:
    """Maps file stem (file name without extension) -> every collected
    file name sharing that stem, in the order given (typically collection
    order) -- a list, not a single file name, since a stem is not
    guaranteed unique across a project.

    Usually a list of one, but a header/implementation pair sharing a base
    name (Config.h + Config.cpp, a normal, extremely common C/C++
    convention) is a real exception, not a hypothetical one -- caught
    while adding C++ support, where this used to be a plain {stem: name}
    dict. That shape let whichever file was processed last silently
    overwrite the other's entry, making the *other* file invisible to
    resolve_dependency()'s exact-filename check (its own name simply
    stopped appearing anywhere in the map's values) -- observed directly:
    a README mentioning "Config.cpp" by its exact name resolved as
    external, purely because "Config.h" happened to win the same stem in
    that particular collection order.
    """
    stem_map: dict[str, list[str]] = {}
    for name in file_names:
        stem_map.setdefault(Path(name).stem, []).append(name)
    return stem_map


# Extensions _pick_candidate() prefers when a bare-stem dependency (no
# filename/extension survives to this point -- a stem-normalized
# #include, or a raw dotted import's last segment) has more than one
# same-stem file to choose between. A header extension wins: an #include
# (or any equivalent path-based import) overwhelmingly names what's being
# *declared*, never what implements it -- C/C++'s Config.h + Config.cpp
# is the motivating, extremely common case. Harmless for every other
# currently-supported language, none of which produce this kind of
# same-stem extension collision today.
_HEADER_EXTENSIONS = (".h", ".hpp", ".hh", ".hxx")


def _flatten_stem_map(stem_map: dict) -> set:
    """Flat set of every file name appearing anywhere in stem_map's value
    lists -- every file name appears in exactly one stem group (its own),
    so this is just their union. Exists so resolve_dependency()'s
    exact-filename check can be an O(1) set lookup instead of scanning
    every stem group on every single call: a caller that resolves many
    dependencies against the same stem_map (build_tree/has_cycle/
    move_file/add_dependency/remove_dependency, each already building
    stem_map once up front) builds this once too and passes it through,
    turning what used to be an O(files) rescan per dependency into an
    O(1) lookup -- the difference between an O(N) and an O(N^2) graph
    rebuild on a project with many files.
    """
    return {name for names in stem_map.values() for name in names}


def _pick_candidate(candidates: list[str]) -> str:
    """Disambiguates resolve_dependency()'s bare-stem match when more than
    one collected file shares that stem. A single candidate returns
    immediately -- the overwhelmingly common case, and the only case that
    existed at all before same-stem collisions were even distinguished
    from an unambiguous match. Several candidates prefer a header
    extension (see _HEADER_EXTENSIONS); failing that, the first one in
    collection order, same as build_stem_map()'s old last-write-wins
    behavior would have picked anyway for two same-kind files (e.g. two
    unrelated "config" stems that aren't a header/impl pair at all).
    """
    if len(candidates) == 1:
        return candidates[0]
    for name in candidates:
        if Path(name).suffix in _HEADER_EXTENSIONS:
            return name
    return candidates[0]


def resolve_dependency(dep: str, stem_map: dict, all_names: set | None = None) -> str | None:
    """Matches a dependencies entry against an internal project file name.

    all_names is the flat set _flatten_stem_map(stem_map) would build --
    pass it explicitly when resolving many dependencies against the same
    stem_map (this function's own docstring on the exact-filename check
    below explains why); left as None and computed on the fly here for a
    one-off caller (a test, corrector.py's interactive loop) that doesn't
    already have one lying around.

    dep can come in three shapes:
    - a raw dotted import path extracted by Tree-sitter (e.g.
      "extract.code.extractor") -- resolved by its last "."-separated
      segment, since that's the actual module/class name the rest of the
      path just namespaces.
    - an already-pinned file name from a prior move in correct_relationships
      (e.g. "extractor.py")
    - an already-normalized bare file stem from a dependency_handler whose
      language expresses imports as file *paths* rather than dotted module
      names (e.g. GDScript's preload("res://scripts/config.gd"), reduced by
      _gdscript_dependency_handler to "config" before it ever reaches here).

    The third shape is why this checks stem_map's keys directly before
    falling back to the split-on-"." heuristic: that heuristic assumes any
    "." in dep is a module-path separator, which is wrong once dep is
    already a bare stem -- a stem that itself contains a literal "."
    (e.g. "player.controller" from player.controller.gd, a real Godot
    variant/state-script naming pattern) would otherwise get re-split and
    truncated to "controller", matching nothing.

    Returns the file name on a match, or None for an external dependency.
    An exact-filename dep (the first check below) always matches its own
    name regardless of how many other files share its stem -- only a
    bare-stem match (the second and third checks) needs _pick_candidate()
    to disambiguate multiple same-stem files.
    """
    if dep in (all_names if all_names is not None else _flatten_stem_map(stem_map)):
        return dep
    candidates = stem_map.get(dep) or stem_map.get(dep.split(".")[-1])
    return _pick_candidate(candidates) if candidates else None


def _drop_text_dep(data: dict, key: str, keep) -> None:
    """In-place filters data[key] (a list) down to entries `keep` accepts,
    but only if the list is present and non-empty -- a no-op write is never
    the observable difference between "had none" and "had some, now none".

    Shared by every place that must keep a text-reference-provenance shadow
    list (`files[name]["text_dependencies"]`, or its finalized counterpart
    `relationships[name]["internal_text_refs"]`) in sync as the real edge
    list it shadows gets edited: move_file/add_dependency/remove_dependency
    below (over `text_dependencies`) and remove_relationship (over
    `internal_text_refs`). Centralized here specifically because this sync
    rule is easy to add a new caller of without remembering it exists at
    all -- see build_tree()'s own docstring for what these lists represent.
    """
    values = data.get(key)
    if values:
        data[key] = [v for v in values if keep(v)]


class CycleError(Exception):
    """Raised by move_file()/add_dependency() when adding an edge would
    create a dependency cycle. from_file/to_file name the edge that was
    attempted (from_file would depend on to_file), regardless of which
    function's own internal has_cycle() call order produced it.
    """

    def __init__(self, from_file: str, to_file: str):
        self.from_file = from_file
        self.to_file = to_file
        super().__init__(f"{from_file!r} depending on {to_file!r} would create a cycle")


def has_cycle(
    files: dict, stem_map: dict, from_file: str, to_file: str, all_names: set | None = None
) -> bool:
    """Checks whether from_file already (transitively) depends on to_file.

    move_file() is about to add the edge "to_file depends on from_file" (a
    dependency entry is a "child" in the tree: see build_tree()/print_tree()).
    That edge closes a cycle exactly when from_file can already reach to_file
    by walking its own existing dependency chain (from_file -> ... -> to_file)
    -- the new edge would then complete the loop to_file -> from_file -> ...
    -> to_file. So this walks from from_file, not to_file.

    all_names is resolve_dependency()'s own same-named param -- computed
    once here (not per dependency) when the caller doesn't already have one,
    since this walk can call resolve_dependency() many times over a large
    project.
    """
    if all_names is None:
        all_names = _flatten_stem_map(stem_map)
    visited = set()
    queue = [from_file]
    while queue:
        current = queue.pop()
        if current == to_file:
            return True
        if current in visited:
            continue
        visited.add(current)
        for dep in files.get(current, {}).get("dependencies", []):
            matched = resolve_dependency(dep, stem_map, all_names)
            if matched:
                queue.append(matched)
    return False


def move_file(files: dict, file_name: str, new_parent: str) -> dict:
    """Reparents file_name under new_parent, removing it from wherever it
    currently sits in the dependency tree first -- regardless of whether its
    old entry there was a raw import path or an already-pinned file name from
    an earlier move.

    Raises ValueError if file_name/new_parent aren't both in `files` (or are
    the same file), and CycleError if the move would create a cycle. Mutates
    and returns `files`.
    """
    if file_name not in files:
        raise ValueError(f"unknown file: {file_name}")
    if new_parent not in files:
        raise ValueError(f"unknown file: {new_parent}")
    if file_name == new_parent:
        raise ValueError("a file can't be its own parent")

    stem_map = build_stem_map(files.keys())
    all_names = _flatten_stem_map(stem_map)
    if has_cycle(files, stem_map, file_name, new_parent, all_names):
        # the edge this move adds is new_parent -> file_name (new_parent now
        # depends on file_name), so that's the edge CycleError should name --
        # not (file_name, new_parent), which is has_cycle()'s own internal
        # walk direction, not the human-facing edge.
        raise CycleError(new_parent, file_name)

    # remove file_name from wherever it's currently listed as a dependency
    for data in files.values():
        deps = data.get("dependencies", [])
        data["dependencies"] = [d for d in deps if resolve_dependency(d, stem_map, all_names) != file_name]
        # text_dependencies is always an exact-filename subset of
        # dependencies (see build_tree()'s docstring) -- a plain equality
        # check keeps it in sync without needing resolve_dependency() again.
        _drop_text_dep(data, "text_dependencies", lambda d: d != file_name)

    files.setdefault(new_parent, {}).setdefault("dependencies", [])
    files[new_parent]["dependencies"].append(file_name)

    return files


def add_dependency(files: dict, file_name: str, target: str) -> dict:
    """Adds a single dependency edge file_name -> target (file_name now
    depends on target) onto file_name's own `dependencies` list only -- no
    side effects on any other file's list, unlike move_file() which strips
    file_name from every existing reference first. This is the "link" half
    of the GUI's per-edge relationship editor: since a file can legitimately
    be depended on by more than one other file, editing one edge shouldn't
    touch any of the others.

    A no-op if the edge already exists (matched by resolved target, so a raw
    import path and an already-pinned file name for the same file don't
    both get added) -- except that a human explicitly (re-)linking an edge
    that already exists purely as a text_references.py text reference (see
    build_tree()'s `internal_text_refs`) upgrades it to a fully certain
    edge instead of doing nothing. That upgrade *is* the human-correction
    step the project treats as its own differentiator -- a no-op here would
    silently leave a human-confirmed edge flagged as a weaker prose mention.
    Raises ValueError if file_name/target aren't both in `files` (or are the
    same file), and CycleError if the edge would close a dependency cycle --
    the same has_cycle() guard move_file() uses.
    """
    if file_name not in files:
        raise ValueError(f"unknown file: {file_name}")
    if target not in files:
        raise ValueError(f"unknown file: {target}")
    if file_name == target:
        raise ValueError("a file can't depend on itself")

    stem_map = build_stem_map(files.keys())
    all_names = _flatten_stem_map(stem_map)
    # the edge being added is file_name -> target; it closes a cycle exactly
    # when target can already (transitively) reach file_name.
    if has_cycle(files, stem_map, target, file_name, all_names):
        raise CycleError(file_name, target)

    deps = files[file_name].setdefault("dependencies", [])
    if not any(resolve_dependency(d, stem_map, all_names) == target for d in deps):
        deps.append(target)
    else:
        _drop_text_dep(
            files[file_name],
            "text_dependencies",
            lambda d: resolve_dependency(d, stem_map, all_names) != target,
        )

    return files


def remove_dependency(files: dict, file_name: str, target: str) -> dict:
    """Removes the dependency edge file_name -> target from file_name's own
    `dependencies` list (matched by resolved target, same as move_file()'s
    matching -- works whether the underlying entry is a raw import path or
    an already-pinned file name). The "unlink" half of the GUI's per-edge
    relationship editor, add_dependency()'s counterpart.

    A no-op if no such edge exists. Raises ValueError if file_name isn't in
    `files`. Mutates and returns `files`.
    """
    if file_name not in files:
        raise ValueError(f"unknown file: {file_name}")

    stem_map = build_stem_map(files.keys())
    all_names = _flatten_stem_map(stem_map)
    deps = files[file_name].get("dependencies", [])
    files[file_name]["dependencies"] = [
        d for d in deps if resolve_dependency(d, stem_map, all_names) != target
    ]

    _drop_text_dep(
        files[file_name], "text_dependencies", lambda d: resolve_dependency(d, stem_map, all_names) != target
    )

    return files


def has_relationship_cycle(relationships: dict, from_file: str, to_file: str) -> bool:
    """has_cycle()'s counterpart over an already-*resolved* `relationships`
    dict (build_tree()'s output, or aif.json's own `relationships` field)
    instead of a `files` dict with raw `dependencies` strings -- no
    stem_map/resolve_dependency step needed, since there's no raw import
    text left to resolve once `dependencies` has already been reduced to
    this shape. Same walk-from-from_file direction and same reasoning as
    has_cycle() -- see that function's docstring.
    """
    visited = set()
    queue = [from_file]
    while queue:
        current = queue.pop()
        if current == to_file:
            return True
        if current in visited:
            continue
        visited.add(current)
        queue.extend(relationships.get(current, {}).get("internal", []))
    return False


def add_relationship(relationships: dict, file_name: str, target: str) -> dict:
    """add_dependency()'s counterpart for editing an already-*finalized*
    `relationships` dict directly -- e.g. the GUI's post-pack relationship
    editor (gui/pack_service.link_saved_relationship()), for a project
    that's already been packed and saved. finalize_aif() prunes each file's
    working-state `dependencies` list once `relationships` is built (see its
    own docstring), so add_dependency() has nothing left to edit on an
    already-saved aif.json -- this operates on `relationships` itself
    instead, which is exactly what's still there.

    Same contract as add_dependency(): a no-op if the edge already exists,
    ValueError for an unknown/self file_name/target, CycleError (via
    has_relationship_cycle()) if the edge would close a cycle. Mutates and
    returns `relationships`.
    """
    if file_name not in relationships:
        raise ValueError(f"unknown file: {file_name}")
    if target not in relationships:
        raise ValueError(f"unknown file: {target}")
    if file_name == target:
        raise ValueError("a file can't depend on itself")

    # the edge being added is file_name -> target; it closes a cycle exactly
    # when target can already (transitively) reach file_name.
    if has_relationship_cycle(relationships, target, file_name):
        raise CycleError(file_name, target)

    internal = relationships[file_name].setdefault("internal", [])
    if target not in internal:
        internal.append(target)
    else:
        # Same upgrade add_dependency() performs (see its own docstring):
        # a human explicitly (re-)linking an edge already present only as
        # an internal_text_refs prose mention is exactly the confirmation
        # that should promote it to a fully certain edge, not a silent
        # no-op that leaves it flagged as the weaker text-reference kind.
        _drop_text_dep(relationships[file_name], "internal_text_refs", lambda d: d != target)

    return relationships


def remove_relationship(relationships: dict, file_name: str, target: str) -> dict:
    """remove_dependency()'s counterpart for an already-finalized
    `relationships` dict -- see add_relationship()'s docstring for why a
    separate pair of functions exists for this shape. A no-op if no such
    edge exists. Raises ValueError if file_name isn't in `relationships`.
    """
    if file_name not in relationships:
        raise ValueError(f"unknown file: {file_name}")

    internal = relationships[file_name].get("internal", [])
    relationships[file_name]["internal"] = [d for d in internal if d != target]

    _drop_text_dep(relationships[file_name], "internal_text_refs", lambda d: d != target)

    return relationships


def build_tree(files: dict) -> dict:
    """Splits each file's `dependencies` into `internal`/`external`, plus a
    third `internal_text_refs` list -- the subset of `internal` reached
    *only* via text_references.py's free filename-mention matching (a
    README naming another file by name, a Godot scene's ext_resource path),
    never via a real Tree-sitter-resolved import to the same target. A
    target reached by both counts as a genuine edge, not a text ref, since
    the code import alone already justifies its place in `internal` -- this
    is why classification happens per resolved *target*, not per raw
    `dependencies` entry (a file can have both an import and a prose mention
    of the same other file).

    packager.py records which raw entries came from a text-reference match
    in each file's own `text_dependencies` (a subset of `dependencies`,
    exact collected file names only, never raw import syntax) -- this is
    what `internal_text_refs` is actually computed from. Closes the
    limitation text_references.py's own docstring documents: a prose mention
    and a structural reference used to land in the same `internal` list with
    no way to tell them apart. See get_dependents()/get_blast_radius()'s
    `include_text_refs` param for the consumer side of this.
    """
    stem_map = build_stem_map(files.keys())
    all_names = _flatten_stem_map(stem_map)
    tree = {}

    for name, data in files.items():
        deps = data.get("dependencies", [])
        text_deps = set(data.get("text_dependencies", []))
        internal, external = [], []
        code_targets, text_only_targets = set(), set()
        for dep in deps:
            matched = resolve_dependency(dep, stem_map, all_names)
            if matched == name:
                continue  # a file referencing itself isn't a real relationship in either direction
            if matched:
                internal.append(matched)
                (text_only_targets if dep in text_deps else code_targets).add(matched)
            else:
                external.append(dep)
        internal = list(dict.fromkeys(internal))   # keep order, drop duplicates
        tree[name] = {
            "internal": internal,
            "external": list(dict.fromkeys(external)),
            "internal_text_refs": [m for m in internal if m in text_only_targets - code_targets],
        }

    return tree


def get_dependents(relationships: dict, file: str, *, include_text_refs: bool = True) -> list[str]:
    """Every file whose `internal` list includes `file` -- i.e. who would be
    directly affected by a change to `file`. The inverse of
    relationships[file]["internal"] (what `file` depends on): this answers
    "who depends on me," not "what do I depend on."

    Takes `relationships` (build_tree()'s output, or aif.json's
    `relationships` field directly), not `files` -- there's no dependency
    resolution left to do here, just a graph traversal over already-resolved
    edges.

    include_text_refs=False excludes a dependent whose only link to `file`
    is a text_references.py filename mention (build_tree()'s
    `internal_text_refs`) -- a "certain relationships only" view for a
    caller that wants to skip the weaker prose-mention signal entirely, not
    just see it flagged.
    """
    result = []
    for name, deps in relationships.items():
        if file not in deps.get("internal", []):
            continue
        if not include_text_refs and file in deps.get("internal_text_refs", []):
            continue
        result.append(name)
    return sorted(result)


def get_blast_radius(relationships: dict, file: str, *, include_text_refs: bool = True) -> list[str]:
    """Transitive closure of get_dependents(): every file that would be
    affected, directly or indirectly, by a change to `file`.

    Can include `file` itself if it participates in a dependency cycle (e.g.
    a <-> b: a change to `a` can transitively come back around through `b`).
    That's correct, not a bug -- verified against a real project where two
    Java classes import each other (a mod's main class and one of its
    registries, a common pattern) and both showed up in each other's blast
    radius, themselves included.

    include_text_refs is get_dependents()'s own param, threaded through
    every hop of the traversal -- a text-reference edge excluded at the
    first hop must stay excluded from the whole transitive walk, not just
    the direct-dependents step.
    """
    visited: set[str] = set()
    queue = [file]
    while queue:
        current = queue.pop()
        for dependent in get_dependents(relationships, current, include_text_refs=include_text_refs):
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)
    return sorted(visited)


def print_tree(tree: dict):
    print("\n📦 Project Dependency Tree\n")

    all_children = set()
    for deps in tree.values():
        all_children.update(deps["internal"])

    # start from files nobody references (if it's all one cycle, treat everything as root)
    roots = [name for name in tree if name not in all_children] or list(tree.keys())

    def print_node(name, ancestors, depth=1):
        indent = "  " * depth
        deps = tree.get(name, {"internal": [], "external": [], "internal_text_refs": []})
        text_refs = set(deps.get("internal_text_refs", []))

        for child in deps["internal"]:
            # a real import vs. just a filename mentioned in prose (see
            # build_tree()'s docstring) -- flagged the same way a cycle is,
            # since both are "still an edge, but read this differently."
            note = " (텍스트 언급)" if child in text_refs else ""
            if child in ancestors:
                print(f"{indent}└── 📄 {child} (순환 참조 → 생략)")
                continue
            print(f"{indent}└── 📄 {child}{note}")
            print_node(child, ancestors | {child}, depth + 1)

        for external in deps["external"]:
            print(f"{indent}└── 📦 {external}")

    for name in roots:
        print(f"├── 📄 {name}")
        print_node(name, {name})
