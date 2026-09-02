import pytest

from ziplex.file.relationship import (
    build_tree, has_cycle, move_file, add_dependency, remove_dependency, build_stem_map, CycleError,
    get_dependents, get_blast_radius, has_relationship_cycle, add_relationship, remove_relationship,
    resolve_dependency,
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


def test_resolve_dependency_matches_exact_filename_even_with_a_stem_collision():
    # A text-reference match (text_references.py) or an already-pinned
    # move_file() name is always an exact filename, not a bare stem -- it
    # must resolve to itself regardless of how many other files share its
    # stem, since this is the first check resolve_dependency() makes.
    stem_map = build_stem_map(["Config.h", "Config.cpp"])
    assert resolve_dependency("Config.cpp", stem_map) == "Config.cpp"
    assert resolve_dependency("Config.h", stem_map) == "Config.h"


def test_resolve_dependency_prefers_header_extension_for_a_bare_stem_collision():
    # A bare-stem dependency (a stem-normalized #include, or a raw dotted
    # import's last segment) carries no extension information by the time
    # it reaches resolve_dependency() -- when more than one file shares
    # that stem, an #include overwhelmingly names what's *declared*
    # (the header), never what implements it.
    stem_map = build_stem_map(["Config.cpp", "Config.h"])  # .cpp collected first
    assert resolve_dependency("Config", stem_map) == "Config.h"


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


def test_has_cycle_detects_would_be_cycle():
    # b already depends on a; making a depend on b too (moving b under a)
    # would close a -> b -> a
    files = {
        "a.py": {"dependencies": []},
        "b.py": {"dependencies": ["a"]},
    }
    stem_map = build_stem_map(files.keys())
    assert has_cycle(files, stem_map, "b.py", "a.py") is True
    # the reverse isn't a cycle: a doesn't depend on anything yet
    assert has_cycle(files, stem_map, "a.py", "b.py") is False


def test_has_cycle_detects_transitive_cycle_through_a_third_file():
    # z -> y -> x already; moving z under x would close x -> z -> y -> x
    files = {
        "x.py": {"dependencies": []},
        "y.py": {"dependencies": ["x"]},
        "z.py": {"dependencies": ["y"]},
    }
    stem_map = build_stem_map(files.keys())
    assert has_cycle(files, stem_map, "z.py", "x.py") is True


def test_move_file_reparents_and_removes_from_old_parent():
    files = {
        "a.py": {"dependencies": ["b"]},
        "b.py": {"dependencies": []},
        "c.py": {"dependencies": []},
    }
    move_file(files, "b.py", "c.py")

    assert "b" not in files["a.py"]["dependencies"]
    assert files["c.py"]["dependencies"] == ["b.py"]


def test_move_file_strips_the_moved_file_from_text_dependencies_too():
    files = {
        "a.py": {"dependencies": ["b"], "text_dependencies": []},
        "readme.md": {"dependencies": ["b.py"], "text_dependencies": ["b.py"]},
        "b.py": {"dependencies": []},
        "c.py": {"dependencies": []},
    }
    move_file(files, "b.py", "c.py")

    assert files["readme.md"]["text_dependencies"] == []
    assert files["c.py"]["dependencies"] == ["b.py"]
    # the new edge under c.py is a real reparent, not a text reference
    assert "text_dependencies" not in files["c.py"] or files["c.py"]["text_dependencies"] == []


def test_move_file_raises_on_cycle():
    files = {
        "a.py": {"dependencies": []},
        "b.py": {"dependencies": ["a"]},  # b depends on a
    }
    with pytest.raises(CycleError):
        move_file(files, "b.py", "a.py")  # would make a depend on b too -> a <-> b


def test_move_file_raises_on_unknown_or_self():
    files = {"a.py": {"dependencies": []}, "b.py": {"dependencies": []}}

    with pytest.raises(ValueError):
        move_file(files, "a.py", "a.py")

    with pytest.raises(ValueError):
        move_file(files, "missing.py", "b.py")


def test_add_dependency_only_touches_the_one_file():
    files = {
        "a.py": {"dependencies": []},
        "b.py": {"dependencies": ["c"]},  # b already depends on c
        "c.py": {"dependencies": []},
    }
    add_dependency(files, "a.py", "c.py")

    assert files["a.py"]["dependencies"] == ["c.py"]
    assert files["b.py"]["dependencies"] == ["c"]  # untouched -- unlike move_file()


def test_add_dependency_is_a_noop_when_the_edge_already_exists():
    files = {
        "a.py": {"dependencies": ["c"]},  # raw import-path form
        "c.py": {"dependencies": []},
    }
    add_dependency(files, "a.py", "c.py")  # already-resolved form of the same edge

    assert files["a.py"]["dependencies"] == ["c"]  # not duplicated


def test_add_dependency_upgrades_an_existing_text_reference_to_a_certain_edge():
    # b.py's dependency on c is already there, but only via a text
    # reference -- a human explicitly linking the same edge in the GUI
    # should confirm it, not leave it flagged as a weaker prose mention.
    files = {
        "b.py": {"dependencies": ["c.py"], "text_dependencies": ["c.py"]},
        "c.py": {"dependencies": []},
    }
    add_dependency(files, "b.py", "c.py")

    assert files["b.py"]["dependencies"] == ["c.py"]  # still a no-op on the edge itself
    assert files["b.py"]["text_dependencies"] == []  # but no longer flagged as text-only


def test_add_dependency_raises_on_cycle():
    files = {
        "a.py": {"dependencies": []},
        "b.py": {"dependencies": ["a"]},  # b already depends on a
    }
    with pytest.raises(CycleError):
        add_dependency(files, "a.py", "b.py")  # would close a -> b -> a


def test_add_dependency_raises_on_unknown_or_self():
    files = {"a.py": {"dependencies": []}, "b.py": {"dependencies": []}}

    with pytest.raises(ValueError):
        add_dependency(files, "a.py", "a.py")
    with pytest.raises(ValueError):
        add_dependency(files, "a.py", "missing.py")


def test_remove_dependency_removes_only_that_edge():
    files = {
        "a.py": {"dependencies": ["b", "c"]},
        "b.py": {"dependencies": []},
        "c.py": {"dependencies": []},
    }
    remove_dependency(files, "a.py", "b.py")

    assert files["a.py"]["dependencies"] == ["c"]


def test_remove_dependency_also_clears_a_matching_text_dependency():
    files = {
        "a.py": {"dependencies": ["b", "c"], "text_dependencies": ["c"]},
        "b.py": {"dependencies": []},
        "c.py": {"dependencies": []},
    }
    remove_dependency(files, "a.py", "c.py")

    assert files["a.py"]["dependencies"] == ["b"]
    assert files["a.py"]["text_dependencies"] == []


def test_remove_dependency_is_a_noop_when_no_such_edge():
    files = {"a.py": {"dependencies": []}, "b.py": {"dependencies": []}}
    remove_dependency(files, "a.py", "b.py")

    assert files["a.py"]["dependencies"] == []


def test_remove_dependency_raises_on_unknown_file():
    files = {"a.py": {"dependencies": []}}
    with pytest.raises(ValueError):
        remove_dependency(files, "missing.py", "a.py")


def test_has_relationship_cycle_detects_would_be_cycle():
    # same scenario as test_has_cycle_detects_would_be_cycle(), but over an
    # already-resolved relationships dict (no stem_map/raw import strings)
    relationships = {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": ["a.py"], "external": []},  # b already depends on a
    }
    assert has_relationship_cycle(relationships, "b.py", "a.py") is True
    assert has_relationship_cycle(relationships, "a.py", "b.py") is False


def test_add_relationship_only_touches_the_one_file():
    relationships = {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": ["c.py"], "external": []},
        "c.py": {"internal": [], "external": []},
    }
    add_relationship(relationships, "a.py", "c.py")

    assert relationships["a.py"]["internal"] == ["c.py"]
    assert relationships["b.py"]["internal"] == ["c.py"]  # untouched


def test_add_relationship_is_a_noop_when_the_edge_already_exists():
    relationships = {
        "a.py": {"internal": ["c.py"], "external": []},
        "c.py": {"internal": [], "external": []},
    }
    add_relationship(relationships, "a.py", "c.py")

    assert relationships["a.py"]["internal"] == ["c.py"]  # not duplicated


def test_add_relationship_raises_on_cycle():
    relationships = {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": ["a.py"], "external": []},  # b already depends on a
    }
    with pytest.raises(CycleError):
        add_relationship(relationships, "a.py", "b.py")  # would close a -> b -> a


def test_add_relationship_raises_on_unknown_or_self():
    relationships = {"a.py": {"internal": [], "external": []}, "b.py": {"internal": [], "external": []}}

    with pytest.raises(ValueError):
        add_relationship(relationships, "a.py", "a.py")
    with pytest.raises(ValueError):
        add_relationship(relationships, "a.py", "missing.py")


def test_remove_relationship_removes_only_that_edge():
    relationships = {
        "a.py": {"internal": ["b.py", "c.py"], "external": []},
        "b.py": {"internal": [], "external": []},
        "c.py": {"internal": [], "external": []},
    }
    remove_relationship(relationships, "a.py", "b.py")

    assert relationships["a.py"]["internal"] == ["c.py"]


def test_remove_relationship_also_clears_a_matching_internal_text_ref():
    relationships = {
        "readme.md": {"internal": ["a.py", "b.py"], "external": [], "internal_text_refs": ["a.py"]},
        "a.py": {"internal": [], "external": [], "internal_text_refs": []},
        "b.py": {"internal": [], "external": [], "internal_text_refs": []},
    }
    remove_relationship(relationships, "readme.md", "a.py")

    assert relationships["readme.md"]["internal"] == ["b.py"]
    assert relationships["readme.md"]["internal_text_refs"] == []


def test_remove_relationship_is_a_noop_when_no_such_edge():
    relationships = {"a.py": {"internal": [], "external": []}, "b.py": {"internal": [], "external": []}}
    remove_relationship(relationships, "a.py", "b.py")

    assert relationships["a.py"]["internal"] == []


def test_remove_relationship_raises_on_unknown_file():
    relationships = {"a.py": {"internal": [], "external": []}}
    with pytest.raises(ValueError):
        remove_relationship(relationships, "missing.py", "a.py")


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
