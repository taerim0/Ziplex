import pytest

from ziplex.edits import (
    set_project_name,
    set_project_prompt,
    add_rule,
    remove_rule,
    set_rules,
    set_file_summary,
    set_folder_summary,
    finalize_aif,
)


def _make_aif():
    return {
        "project": {"name": "old-name", "prompt": "old prompt"},
        "rules": ["rule one"],
        "folders": {".": {"summary": "old folder summary"}},
        "files": {
            "a.py": {
                "summary": "old summary",
                "signatures": ["def a()"],
                "dependencies": ["b"],
                "api": [],
                "compressed": "def a():\n    ...",
            },
            "b.py": {
                "summary": "b summary",
                "signatures": [],
                "dependencies": [],
                "api": [],
                "compressed": "x = 1",
            },
        },
    }


def test_set_project_name_and_prompt():
    aif = _make_aif()
    set_project_name(aif, "new-name")
    set_project_prompt(aif, "new prompt")
    assert aif["project"]["name"] == "new-name"
    assert aif["project"]["prompt"] == "new prompt"


def test_add_and_remove_rule():
    aif = _make_aif()
    add_rule(aif, "rule two")
    assert aif["rules"] == ["rule one", "rule two"]

    remove_rule(aif, 0)
    assert aif["rules"] == ["rule two"]


def test_remove_rule_out_of_range_raises():
    aif = _make_aif()
    with pytest.raises(IndexError):
        remove_rule(aif, 5)


def test_set_rules_replaces_the_whole_list():
    aif = _make_aif()
    set_rules(aif, ["brand new rule", "another one"])
    assert aif["rules"] == ["brand new rule", "another one"]


def test_set_rules_with_empty_list_clears_rules():
    aif = _make_aif()
    set_rules(aif, [])
    assert aif["rules"] == []


def test_set_file_summary():
    aif = _make_aif()
    set_file_summary(aif, "a.py", "new summary")
    assert aif["files"]["a.py"]["summary"] == "new summary"


def test_set_file_summary_unknown_file_raises():
    aif = _make_aif()
    with pytest.raises(KeyError):
        set_file_summary(aif, "missing.py", "summary")


def test_set_folder_summary():
    aif = _make_aif()
    set_folder_summary(aif, ".", "new folder summary")
    assert aif["folders"]["."]["summary"] == "new folder summary"


def test_set_folder_summary_unknown_folder_raises():
    aif = _make_aif()
    with pytest.raises(KeyError):
        set_folder_summary(aif, "missing/folder", "summary")


def test_finalize_aif_builds_relationships_and_prunes_working_fields():
    aif = _make_aif()
    finalize_aif(aif)

    assert aif["relationships"]["a.py"] == {"internal": ["b.py"], "external": [], "internal_text_refs": []}

    for data in aif["files"].values():
        assert "signatures" not in data
        assert "dependencies" not in data
        assert "api" not in data
        # summary/compressed are what actually ships -- must survive
        assert "summary" in data
        assert "compressed" in data


def test_finalize_aif_tags_a_text_reference_edge_and_prunes_text_dependencies():
    aif = _make_aif()
    # README.md's "b.py" mention got merged into dependencies the same way
    # packager.py does it, plus recorded separately as text_dependencies --
    # see packager.py's own merge-step comment and file/relationship.py's
    # build_tree() docstring.
    aif["files"]["readme.md"] = {
        "summary": "docs",
        "signatures": [],
        "dependencies": ["b.py"],
        "text_dependencies": ["b.py"],
        "api": [],
        "compressed": "See b.py.",
    }
    finalize_aif(aif)

    assert aif["relationships"]["readme.md"]["internal_text_refs"] == ["b.py"]
    # a.py's b.py edge is a real import, not a text reference -- must not
    # be tagged just because some *other* file's edge to the same target is.
    assert aif["relationships"]["a.py"]["internal_text_refs"] == []
    assert "text_dependencies" not in aif["files"]["readme.md"]
