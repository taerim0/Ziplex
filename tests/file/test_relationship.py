import pytest

from ziplex.file.relationship import (
    build_tree, build_stem_map, get_dependents, get_blast_radius, resolve_dependency, _flatten_stem_map,
)


def test_build_tree_splits_internal_and_external():
    files = {
        "a.py": {"dependencies": ["b", "os"]},
        "b.py": {"dependencies": []},
    }
    tree = build_tree(files)
    assert tree["a.py"] == {"internal": ["b.py"], "external": ["os"], "internal_text_refs": []}
    assert tree["b.py"] == {"internal": [], "external": [], "internal_text_refs": []}


def test_build_tree_tags_a_text_reference_edge_as_internal_text_refs():
    # README.md mentions b.py by name (text_references.py), with no real
    # import behind it -- packager.py records that as text_dependencies, a
    # subset of dependencies.
    files = {
        "README.md": {"dependencies": ["b.py"], "text_dependencies": ["b.py"]},
        "b.py": {"dependencies": []},
    }
    tree = build_tree(files)
    assert tree["README.md"]["internal"] == ["b.py"]
    assert tree["README.md"]["internal_text_refs"] == ["b.py"]


def test_build_tree_does_not_tag_an_edge_backed_by_a_real_import_too():
    # a.py imports b (raw import syntax) *and* separately text-mentions
    # "b.py" by its exact name -- both resolve to the same target, so the
    # edge is a genuine import, not merely a text reference, even though one
    # of the two raw dependency strings is flagged in text_dependencies.
    files = {
        "a.py": {"dependencies": ["b", "b.py"], "text_dependencies": ["b.py"]},
        "b.py": {"dependencies": []},
    }
    tree = build_tree(files)
    assert tree["a.py"]["internal"] == ["b.py"]
    assert tree["a.py"]["internal_text_refs"] == []


def test_get_dependents_can_exclude_text_reference_only_edges():
    relationships = {
        "a.py": {"internal": [], "external": [], "internal_text_refs": []},
        "readme.md": {"internal": ["a.py"], "external": [], "internal_text_refs": ["a.py"]},
        "b.py": {"internal": ["a.py"], "external": [], "internal_text_refs": []},
    }
    assert get_dependents(relationships, "a.py") == ["b.py", "readme.md"]
    assert get_dependents(relationships, "a.py", include_text_refs=False) == ["b.py"]


def test_get_blast_radius_excludes_a_dependent_reached_only_through_a_text_reference():
    # readme.md -> b.py is a text reference; b.py -> a.py is a real import.
    # Excluding text refs should drop readme.md from a.py's blast radius
    # entirely, not just its own edge.
    relationships = {
        "a.py": {"internal": [], "external": [], "internal_text_refs": []},
        "b.py": {"internal": ["a.py"], "external": [], "internal_text_refs": []},
        "readme.md": {"internal": ["b.py"], "external": [], "internal_text_refs": ["b.py"]},
    }
    assert get_blast_radius(relationships, "a.py") == ["b.py", "readme.md"]
    assert get_blast_radius(relationships, "a.py", include_text_refs=False) == ["b.py"]


def test_build_tree_dedupes_and_excludes_self_reference():
    files = {
        "a.py": {"dependencies": ["b", "b", "a"]},  # duplicate + self-import
        "b.py": {"dependencies": []},
    }
    tree = build_tree(files)
    assert tree["a.py"]["internal"] == ["b.py"]
    # a self-reference must be dropped outright, not fall through into
    # "external" just because it failed the `matched != name` check --
    # this file's test name always claimed this, but never actually
    # asserted the "external" side, so the leak passed undetected.
    assert tree["a.py"]["external"] == []


def test_build_tree_resolves_a_dependency_stem_containing_a_dot():
    # A dependency_handler for a path-based language (e.g. GDScript's
    # preload("res://scripts/player.controller.gd")) normalizes to the bare
    # stem before it ever reaches build_tree/resolve_dependency -- but that
    # stem can itself contain a literal "." (a real Godot variant/state-
    # script naming pattern). resolve_dependency() must match it via an
    # exact stem_map-key lookup, not re-split it on "." as if it were a
    # dotted module path (which would truncate "player.controller" down to
    # just "controller" and silently miss the match).
    files = {
        "player.controller.gd": {"dependencies": ["config"]},
        "config.gd": {"dependencies": []},
        "player.gd": {"dependencies": ["player.controller"]},
    }
    tree = build_tree(files)
    assert tree["player.gd"]["internal"] == ["player.controller.gd"]


def test_build_stem_map_keeps_every_file_sharing_a_stem():
    # A header/implementation pair (Config.h + Config.cpp, a normal,
    # extremely common C/C++ convention) shares a stem -- build_stem_map()
    # must not let one silently overwrite the other's entry the way a
    # plain {stem: name} dict would.
    stem_map = build_stem_map(["Config.h", "Config.cpp", "main.cpp"])
    assert set(stem_map["Config"]) == {"Config.h", "Config.cpp"}
    assert stem_map["main"] == ["main.cpp"]


def test_build_stem_map_aliases_d_mlua_declaration_files():
    # A .d.mlua file's Path.stem is only single-suffix-stripped
    # ("AIComponent.d"), which would never match a real script's own bare
    # `extends AIComponent` (extract/code/languages.py's
    # _mlua_dependency_handler) -- build_stem_map() must also key it under
    # the doubly-stripped "AIComponent".
    stem_map = build_stem_map(["AIComponent.d.mlua", "Health.mlua"])
    assert stem_map["AIComponent.d"] == ["AIComponent.d.mlua"]
    assert stem_map["AIComponent"] == ["AIComponent.d.mlua"]
    assert stem_map["Health"] == ["Health.mlua"]


def test_resolve_dependency_matches_exact_filename_even_with_a_stem_collision():
    # A text-reference match (text_references.py) or an already-pinned
    # move_file() name is always an exact filename, not a bare stem -- it
    # must resolve to itself regardless of how many other files share its
    # stem, since this is the first check resolve_dependency() makes.
    stem_map = build_stem_map(["Config.h", "Config.cpp"])
    assert resolve_dependency("Config.cpp", stem_map) == "Config.cpp"
    assert resolve_dependency("Config.h", stem_map) == "Config.h"


def test_resolve_dependency_accepts_a_precomputed_all_names_set():
    # A code-review finding: resolve_dependency()'s exact-filename check used
    # to rescan every stem_map group on every call (O(files) per call,
    # O(files^2) across a whole build_tree()/has_cycle() walk). Callers that
    # resolve many dependencies against the same stem_map now build this set
    # once and pass it through -- must give the same answer either way.
    stem_map = build_stem_map(["Config.h", "Config.cpp"])
    all_names = _flatten_stem_map(stem_map)
    assert all_names == {"Config.h", "Config.cpp"}
    assert resolve_dependency("Config.cpp", stem_map, all_names) == "Config.cpp"
    assert resolve_dependency("Config.cpp", stem_map) == "Config.cpp"  # still works with all_names omitted


def test_resolve_dependency_prefers_header_extension_for_a_bare_stem_collision():
    # A bare-stem dependency (a stem-normalized #include, or a raw dotted
    # import's last segment) carries no extension information by the time
    # it reaches resolve_dependency() -- when more than one file shares
    # that stem, an #include overwhelmingly names what's *declared*
    # (the header), never what implements it.
    stem_map = build_stem_map(["Config.cpp", "Config.h"])  # .cpp collected first
    assert resolve_dependency("Config", stem_map) == "Config.h"


def test_resolve_dependency_prefers_same_extension_as_importer_for_a_cross_language_stem_collision():
    # A real, confirmed bug in Ziplex's own repo: a bare-stem dependency
    # (e.g. a Python `from . import search`, reduced to the stem "search")
    # can collide with an *unrelated file in a different language* that
    # happens to share the same stem -- here, a JS file living elsewhere in
    # the project. No language imports a sibling module across languages by
    # a bare stem, so the candidate sharing the importing file's own
    # extension must win, regardless of collection order. Without
    # source_name, resolution falls back to collection order (still
    # documented, still correct for a caller with no importer to compare
    # against).
    stem_map = build_stem_map(["gui/js/pages/search.js", "search.py"])  # .js collected first
    assert resolve_dependency("search", stem_map, source_name="cli.py") == "search.py"
    # A bare JS specifier is a package, never a file stem -- only ./-relative
    # or tsconfig-aliased imports reach files (see the `from "react"` test below).
    assert resolve_dependency("search", stem_map, source_name="pages/router.js") is None
    assert resolve_dependency("search", stem_map) == "gui/js/pages/search.js"  # no source -- old fallback


def test_build_tree_resolves_a_bare_stem_dependency_to_the_same_language_sibling():
    # End-to-end regression for the bug above: build_tree() must thread
    # each file's own name through to resolve_dependency() as source_name,
    # so a Python file's dependency on another Python module never lands on
    # an unrelated same-stem file in a different language.
    files = {
        "cli.py": {"dependencies": [".search"]},
        "search.py": {"dependencies": []},
        "gui/js/pages/search.js": {"dependencies": []},
    }
    tree = build_tree(files)
    assert tree["cli.py"]["internal"] == ["search.py"]


def test_resolve_dependency_rejects_a_dotted_import_whose_path_doesnt_align():
    # A real, confirmed bug in Ziplex's own repo: `from ruamel.yaml import
    # YAML` (tests/extract/text/test_text_compressors.py's own genuine
    # external dependency) used to silently resolve onto this project's
    # own extract/text/yaml.py, purely because both dotted paths end in
    # "yaml" -- resolve_dependency()'s bare-stem fallback only ever
    # compared a dotted dep's *last* segment, blind to the rest of the
    # path. A single-segment dep still resolves fine (nothing else to
    # check it against); a multi-segment one must actually align with the
    # candidate's real path, not just its stem.
    stem_map = build_stem_map(["src/ziplex/extract/text/yaml.py"])
    assert resolve_dependency("ruamel.yaml", stem_map) is None
    assert resolve_dependency("ziplex.extract.text.yaml", stem_map) == "src/ziplex/extract/text/yaml.py"
    assert resolve_dependency(".extract.text.yaml", stem_map) == "src/ziplex/extract/text/yaml.py"
    assert resolve_dependency("yaml", stem_map) == "src/ziplex/extract/text/yaml.py"  # bare stem, unaffected


def test_build_tree_keeps_a_same_leaf_name_external_import_external():
    # End-to-end regression for the bug above: a file with both a genuine
    # internal import and an unrelated external import that happens to
    # share its deepest module name must show both -- the external one
    # never silently disappears into the internal edge.
    files = {
        "tests/extract/text/test_text_compressors.py": {
            "dependencies": ["ruamel.yaml", "ziplex.extract.text.yaml"],
        },
        "src/ziplex/extract/text/yaml.py": {"dependencies": []},
    }
    tree = build_tree(files)
    assert tree["tests/extract/text/test_text_compressors.py"]["internal"] == [
        "src/ziplex/extract/text/yaml.py"
    ]
    assert tree["tests/extract/text/test_text_compressors.py"]["external"] == ["ruamel.yaml"]


def test_build_tree_resolves_both_sides_of_a_header_impl_pair(tmp_path):
    # End-to-end regression for the bug this was caught by: a project with
    # a real Config.h/Config.cpp pair, where a third file references
    # *both* by their exact (already-resolved, text-reference-shaped)
    # names -- both must resolve internal, not just whichever one happens
    # to currently occupy the stem_map slot.
    files = {
        "Config.h": {"dependencies": []},
        "Config.cpp": {"dependencies": ["Config.h"]},
        "README.md": {"dependencies": ["Config.h", "Config.cpp"]},
    }
    tree = build_tree(files)
    assert tree["README.md"]["internal"] == ["Config.h", "Config.cpp"]
    assert tree["README.md"]["external"] == []


def test_get_dependents_finds_direct_dependents_only():
    # b and c both depend on a; c also depends on b
    relationships = {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": ["a.py"], "external": []},
        "c.py": {"internal": ["a.py", "b.py"], "external": []},
    }

    assert get_dependents(relationships, "a.py") == ["b.py", "c.py"]
    assert get_dependents(relationships, "b.py") == ["c.py"]
    assert get_dependents(relationships, "c.py") == []


def test_get_blast_radius_is_transitive():
    # c -> b -> a: changing a transitively affects both b and c
    relationships = {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": ["a.py"], "external": []},
        "c.py": {"internal": ["b.py"], "external": []},
    }

    assert get_blast_radius(relationships, "a.py") == ["b.py", "c.py"]
    assert get_blast_radius(relationships, "b.py") == ["c.py"]
    assert get_blast_radius(relationships, "c.py") == []


def test_get_blast_radius_handles_a_diamond_without_duplicates():
    # b and c both depend on a; d depends on both b and c
    relationships = {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": ["a.py"], "external": []},
        "c.py": {"internal": ["a.py"], "external": []},
        "d.py": {"internal": ["b.py", "c.py"], "external": []},
    }

    assert get_blast_radius(relationships, "a.py") == ["b.py", "c.py", "d.py"]


def test_get_blast_radius_includes_self_when_part_of_a_cycle():
    # a <-> b: a mutual import. A change to a can transitively come back
    # around through b, so a legitimately appears in its own blast radius --
    # this isn't a bug, see get_blast_radius()'s docstring.
    relationships = {
        "a.py": {"internal": ["b.py"], "external": []},
        "b.py": {"internal": ["a.py"], "external": []},
    }

    assert get_blast_radius(relationships, "a.py") == ["a.py", "b.py"]


def test_build_tree_resolves_ts_js_relative_imports_against_the_importing_file():
    # "./utils/helpers" was split on "." into "/utils/helpers", matched no
    # stem, and every relative TS/JS import came out external.
    files = {
        "src/main.ts": {"dependencies": ["./utils/helpers", "../lib/x", "./components", "react"]},
        "src/utils/helpers.ts": {"dependencies": []},
        "lib/x.js": {"dependencies": []},
        "src/components/index.tsx": {"dependencies": []},
    }
    tree = build_tree(files)
    assert sorted(tree["src/main.ts"]["internal"]) == ["lib/x.js", "src/components/index.tsx", "src/utils/helpers.ts"]
    assert tree["src/main.ts"]["external"] == ["react"]


def test_resolve_dependency_maps_a_ts_esm_js_specifier_to_its_ts_source():
    stem_map = build_stem_map(["src/a.ts", "src/b.ts"])
    assert resolve_dependency("./b.js", stem_map, source_name="src/a.ts") == "src/b.ts"


def test_resolve_dependency_never_resolves_a_relative_path_by_bare_stem():
    # "./helpers" from src/a.ts must not land on an unrelated other/helpers.ts.
    stem_map = build_stem_map(["src/a.ts", "other/helpers.ts"])
    assert resolve_dependency("./helpers", stem_map, source_name="src/a.ts") is None


def test_resolve_dependency_does_not_match_a_dotted_external_to_a_root_level_file():
    # `ruamel.yaml` aligned its last segment with a root-level yaml.py.
    stem_map = build_stem_map(["yaml.py", "path.py", "pkg/utils.py"])
    assert resolve_dependency("ruamel.yaml", stem_map, source_name="a.py") is None
    assert resolve_dependency("os.path", stem_map, source_name="a.py") is None
    assert resolve_dependency("pkg.utils", stem_map, source_name="a.py") == "pkg/utils.py"
    assert resolve_dependency("yaml", stem_map, source_name="a.py") == "yaml.py"


def test_resolve_dependency_resolves_a_bare_dot_to_the_folders_index():
    # `import { x } from '.'` in src/helper/cookie/index.test.ts
    stem_map = build_stem_map(["src/helper/cookie/index.ts", "src/helper/cookie/index.test.ts", "src/helper/index.ts"])
    assert resolve_dependency(".", stem_map, source_name="src/helper/cookie/index.test.ts") == "src/helper/cookie/index.ts"
    assert resolve_dependency("..", stem_map, source_name="src/helper/cookie/index.test.ts") == "src/helper/index.ts"


def test_resolve_dependency_resolves_python_relative_imports_against_the_package():
    # Packing Ziplex itself: `from .file.relationship` in src/ziplex/query_service.py
    # landed on a stale src/file/relationship.py sharing the same dotted tail.
    names = [
        "src/file/relationship.py", "src/ziplex/file/relationship.py", "src/ziplex/query_service.py",
        "src/ziplex/extract/__init__.py", "src/ziplex/extract/code/parser.py",
    ]
    stem_map = build_stem_map(names)
    src = "src/ziplex/query_service.py"
    assert resolve_dependency(".file.relationship", stem_map, source_name=src) == "src/ziplex/file/relationship.py"
    assert resolve_dependency(".extract", stem_map, source_name=src) == "src/ziplex/extract/__init__.py"
    nested = "src/ziplex/extract/code/parser.py"
    assert resolve_dependency("...file.relationship", stem_map, source_name=nested) == "src/ziplex/file/relationship.py"


def test_resolve_dependency_python_relative_miss_falls_back_to_the_stem_heuristic():
    # `from . import helper` may name a function in __init__.py, not a module file.
    stem_map = build_stem_map(["pkg/a.py", "lib/helper.py"])
    assert resolve_dependency(".helper", stem_map, source_name="pkg/a.py") == "lib/helper.py"


def test_resolve_dependency_never_stem_matches_a_bare_ts_js_package_specifier():
    # create-t3-turbo: `import { cache } from "react"` landed on apps/nextjs/src/trpc/react.tsx.
    stem_map = build_stem_map(["src/trpc/react.tsx", "src/page.tsx", "lib/utils.py"])
    assert resolve_dependency("react", stem_map, source_name="src/page.tsx") is None
    assert resolve_dependency("react", stem_map, source_name="src/page.mjs") is None
    assert resolve_dependency("src/trpc/react.tsx", stem_map, source_name="src/page.tsx") == "src/trpc/react.tsx"
    assert resolve_dependency("utils", stem_map, source_name="main.py") == "lib/utils.py"  # Python unaffected
