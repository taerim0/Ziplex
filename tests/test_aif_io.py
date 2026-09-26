import json

from ziplex import query_service
from ziplex.aif_io import (
    attach_weak_edges,
    detach_weak_edges,
    expand_relationships,
    save_relationships_edit,
)
from ziplex.file.relationship import add_relationship, remove_relationship
from ziplex.packager import save_aif


def _full():
    # a.py <- b.py (real import); README.md mentions a.py in prose (weak);
    # c.py is isolated (entry would be all-empty).
    return {
        "a.py": {"internal": [], "external": ["os"], "internal_text_refs": []},
        "b.py": {"internal": ["a.py"], "external": [], "internal_text_refs": []},
        "README.md": {"internal": ["a.py"], "external": [], "internal_text_refs": ["a.py"]},
        "c.py": {"internal": [], "external": [], "internal_text_refs": []},
    }


def test_detach_moves_weak_edges_out_and_drops_empty_entries():
    slim, weak = detach_weak_edges(_full())

    assert slim == {
        "a.py": {"internal": [], "external": ["os"]},
        "b.py": {"internal": ["a.py"], "external": []},
    }
    assert weak == {"README.md": ["a.py"]}


def test_detach_then_expand_roundtrips_to_the_in_memory_shape():
    full = _full()
    slim, weak = detach_weak_edges(full)

    assert expand_relationships(slim, full.keys(), weak) == full


def test_expand_restores_an_entry_for_every_packed_file():
    # query_service._require_known_file() treats "is a key" as "is a real file"
    slim, _ = detach_weak_edges(_full())

    assert "c.py" in expand_relationships(slim, ["a.py", "b.py", "README.md", "c.py"], {})


def test_detach_is_a_noop_on_an_already_slim_graph():
    slim, _ = detach_weak_edges(_full())

    assert detach_weak_edges(slim)[0] == slim


def test_expand_passes_a_pre_slim_format_entry_through_untouched():
    old = {"a.py": {"internal": ["b.py"], "external": [], "internal_text_refs": ["b.py"]}}

    assert expand_relationships(old, ["a.py"], {"a.py": ["zzz.py"]}) == old


def test_attach_ignores_weak_edges_already_present_as_certain_ones():
    # a human later linked README.md -> a.py for real; the stale detail.json
    # text_refs record must not re-tag it as weak
    aif = {"files": {"README.md": {}}, "relationships": {"README.md": {"internal": ["a.py"], "external": []}}}
    detail = {"README.md": {"compressed": "", "text_refs": ["a.py"]}}

    entry = attach_weak_edges(aif, detail)["relationships"]["README.md"]

    assert entry["internal"] == ["a.py"]
    assert entry["internal_text_refs"] == []


def _aif_with(relationships):
    return {
        "project": {"name": "demo"},
        "files": {n: {"summary": "s", "compressed": f"body of {n}"} for n in relationships},
        "relationships": relationships,
        "_manifest": {n: "h" for n in relationships},
    }


def _save(tmp_path):
    out = tmp_path / "demo.json"
    save_aif(_aif_with(_full()), output_path=str(out), progress_lang="en")
    return out


def test_save_aif_writes_only_certain_edges_and_puts_weak_ones_in_detail(tmp_path):
    out = _save(tmp_path)

    saved = json.loads(out.read_text(encoding="utf-8"))
    detail = json.loads((tmp_path / "demo.detail.json").read_text(encoding="utf-8"))

    assert saved["relationships"] == {
        "a.py": {"internal": [], "external": ["os"]},
        "b.py": {"internal": ["a.py"], "external": []},
    }
    assert "internal_text_refs" not in json.dumps(saved)
    assert detail["README.md"]["text_refs"] == ["a.py"]
    assert "text_refs" not in detail["a.py"]
    assert detail["README.md"]["compressed"] == "body of README.md"


def test_query_service_sees_weak_edges_again_after_a_save(tmp_path):
    out = _save(tmp_path)

    assert query_service.get_dependents(str(out), "a.py") == ["README.md", "b.py"]
    assert query_service.get_dependents(str(out), "a.py", include_text_refs=False) == ["b.py"]
    assert query_service.get_relationships(str(out), include_text_refs=True)["README.md"]["internal_text_refs"] == ["a.py"]
    # an isolated file is still a recognized file, not "not found"
    assert query_service.get_dependents(str(out), "c.py") == []


def test_saved_graph_still_loads_when_detail_json_is_gone(tmp_path):
    out = _save(tmp_path)
    (tmp_path / "demo.detail.json").unlink()

    # degrades to certain edges only -- no error
    assert query_service.get_dependents(str(out), "a.py") == ["b.py"]


def test_edit_roundtrip_keeps_weak_edges_and_touches_nothing_else(tmp_path):
    out = _save(tmp_path)
    before_detail = json.loads((tmp_path / "demo.detail.json").read_text(encoding="utf-8"))

    save_relationships_edit(str(out), lambda rel: add_relationship(rel, "c.py", "a.py"))

    saved = json.loads(out.read_text(encoding="utf-8"))
    after_detail = json.loads((tmp_path / "demo.detail.json").read_text(encoding="utf-8"))
    assert saved["relationships"]["c.py"] == {"internal": ["a.py"], "external": []}
    assert after_detail["README.md"]["text_refs"] == ["a.py"]  # weak edge survived a strong-edge edit
    assert {n: e["compressed"] for n, e in after_detail.items()} == {n: e["compressed"] for n, e in before_detail.items()}


def test_editing_away_a_weak_edge_removes_it_from_detail_json(tmp_path):
    out = _save(tmp_path)

    save_relationships_edit(str(out), lambda rel: remove_relationship(rel, "README.md", "a.py"))

    detail = json.loads((tmp_path / "demo.detail.json").read_text(encoding="utf-8"))
    assert "text_refs" not in detail["README.md"]
    assert query_service.get_dependents(str(out), "a.py") == ["b.py"]


def test_edit_without_a_detail_json_keeps_weak_edges_in_aif_json_instead_of_dropping_them(tmp_path):
    out = _save(tmp_path)
    (tmp_path / "demo.detail.json").unlink()
    # start from a full-shape aif.json, as a pre-slim pack would have left
    aif = json.loads(out.read_text(encoding="utf-8"))
    aif["relationships"] = _full()
    out.write_text(json.dumps(aif), encoding="utf-8")

    save_relationships_edit(str(out), lambda rel: add_relationship(rel, "c.py", "a.py"))

    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["relationships"]["README.md"]["internal_text_refs"] == ["a.py"]


def test_a_pre_slim_aif_json_still_loads_and_queries(tmp_path):
    out = tmp_path / "old.json"
    out.write_text(json.dumps(_aif_with(_full())), encoding="utf-8")

    assert query_service.get_dependents(str(out), "a.py", include_text_refs=False) == ["b.py"]
    assert query_service.get_dependents(str(out), "a.py") == ["README.md", "b.py"]


# --- on-disk layout -------------------------------------------------------

def test_dumps_aif_is_plain_json_that_roundtrips_exactly():
    from ziplex.aif_io import dumps_aif

    aif = {
        "project": {"name": "demo", "prompt": "한국어 ok", "scope": {"include": [], "ignore": []}},
        "rules": ["r1", "r2", "r3"],
        "files": {"a.py": {"summary": "s", "confidence": 0.2}, "b.py": {"summary": "t"}},
        "relationships": {},
        "folders": {},
        "tokens": {"GPT-4o": {"original": 1, "compressed": 1}},
        "language": "ko",
    }

    assert json.loads(dumps_aif(aif)) == aif


def test_dumps_aif_puts_each_file_and_rule_on_its_own_line():
    from ziplex.aif_io import dumps_aif

    aif = {"rules": ["r1", "r2"], "files": {"a.py": {"summary": "x"}, "b.py": {"summary": "y"}}}
    lines = dumps_aif(aif).splitlines()

    # a change to one file/rule touches exactly one line -- what keeps git
    # diffs and merges per-entry instead of whole-file
    assert sum('"a.py"' in line for line in lines) == 1
    assert sum('"b.py"' in line for line in lines) == 1
    assert sum('"r1"' in line for line in lines) == 1


def test_dumps_aif_is_much_smaller_than_indent_2():
    from ziplex.aif_io import dumps_aif

    aif = {"files": {f"f{i}.py": {"summary": "does a thing", "confidence": 0.5} for i in range(50)}}

    assert len(dumps_aif(aif)) < len(json.dumps(aif, indent=2)) * 0.8


def test_every_writer_of_a_saved_aif_uses_the_same_layout(tmp_path):
    out = _save(tmp_path)
    layout_after_save = out.read_text(encoding="utf-8")

    save_relationships_edit(str(out), lambda rel: rel)  # a no-op edit must not reflow the file

    assert out.read_text(encoding="utf-8") == layout_after_save


def test_save_relationships_edit_refuses_to_run_on_an_unreadable_detail_json(tmp_path):
    # A truncated/locked detail.json used to be treated as absent: the weak
    # edges were written inline into aif.json, and expand_relationships()
    # then ignored detail.json's text_refs forever -- every prose-mention
    # edge permanently lost over a transient read error.
    import pytest

    out = _save(tmp_path)
    detail_path = tmp_path / "demo.detail.json"
    good_detail = detail_path.read_text(encoding="utf-8")
    detail_path.write_text(good_detail[: len(good_detail) // 2], encoding="utf-8")
    before = out.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        save_relationships_edit(str(out), lambda rels: rels)
    assert out.read_text(encoding="utf-8") == before  # nothing written

    detail_path.write_text(good_detail, encoding="utf-8")
    rels = query_service.get_relationships(str(out), include_text_refs=True)
    assert rels["README.md"]["internal_text_refs"] == ["a.py"]
