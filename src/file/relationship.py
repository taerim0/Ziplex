from pathlib import Path


def build_stem_map(file_names) -> dict:
    """Maps file stem (file name without extension) -> file name.
    Used to match against the last segment of an import path (e.g. "extract.code.parser")."""
    return {Path(name).stem: name for name in file_names}


def resolve_dependency(dep: str, stem_map: dict) -> str | None:
    """Matches a dependencies entry against an internal project file name.

    dep can come in two shapes:
    - a raw import path extracted by Tree-sitter (e.g. "extract.code.extractor")
    - an already-pinned file name from a prior move in correct_relationships
      (e.g. "extractor.py")

    Returns the file name on a match, or None for an external dependency.
    """
    if dep in stem_map.values():
        return dep
    return stem_map.get(dep.split(".")[-1])


def build_tree(files: dict) -> dict:
    stem_map = build_stem_map(files.keys())
    tree = {}

    for name, data in files.items():
        deps = data.get("dependencies", [])
        internal, external = [], []
        for dep in deps:
            matched = resolve_dependency(dep, stem_map)
            if matched and matched != name:
                internal.append(matched)
            else:
                external.append(dep)
        tree[name] = {
            "internal": list(dict.fromkeys(internal)),   # keep order, drop duplicates
            "external": list(dict.fromkeys(external))
        }

    return tree


def print_tree(tree: dict):
    print("\n📦 Project Dependency Tree\n")

    all_children = set()
    for deps in tree.values():
        all_children.update(deps["internal"])

    # start from files nobody references (if it's all one cycle, treat everything as root)
    roots = [name for name in tree if name not in all_children] or list(tree.keys())

    def print_node(name, ancestors, depth=1):
        indent = "  " * depth
        deps = tree.get(name, {"internal": [], "external": []})

        for child in deps["internal"]:
            if child in ancestors:
                print(f"{indent}└── 📄 {child} (순환 참조 → 생략)")
                continue
            print(f"{indent}└── 📄 {child}")
            print_node(child, ancestors | {child}, depth + 1)

        for external in deps["external"]:
            print(f"{indent}└── 📦 {external}")

    for name in roots:
        print(f"├── 📄 {name}")
        print_node(name, {name})
