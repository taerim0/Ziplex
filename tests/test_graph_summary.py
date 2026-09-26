import json

import pytest

from ziplex import query_service
from ziplex.file.relationship import get_blast_radius, graph_summary
from ziplex.file.textutil import normalize_path
from ziplex.skill_export import generate_skill_files


def _graph():
    return {
        "a.py": {"internal": ["b.py", "README.md"], "external": ["os"], "internal_text_refs": ["README.md"]},
        "b.py": {"internal": ["c.py"], "external": [], "internal_text_refs": []},
        "c.py": {"internal": ["b.py"], "external": [], "internal_text_refs": []},  # b <-> c cycle
        "lone.py": {"internal": [], "external": [], "internal_text_refs": []},
        "pkg/d.py": {"internal": ["b.py"], "external": [], "internal_text_refs": []},
        "README.md": {"internal": ["a.py"], "external": [], "internal_text_refs": ["a.py"]},
    }


def test_graph_summary_ranks_counts_cycles_orphans_and_folder_edges():
    s = graph_summary(_graph(), scope=lambda n: n.endswith(".py"))
    assert s["file_count"] == 5
    assert s["edge_count"] == 4  # a->b, b->c, c->b, pkg/d->b
    assert s["most_depended_on"][0] == {"file": "b.py", "count": 3}
    assert s["cycles"] == [["b.py", "c.py"]]
    assert s["orphans"] == ["lone.py"]
    assert s["never_imported_count"] == 3  # a.py, lone.py, pkg/d.py
    assert s["folder_edges"] == [{"from": "pkg", "to": ".", "count": 1}]


def test_graph_summary_excludes_prose_mentions_unless_asked():
    assert graph_summary(_graph())["text_ref_edges_excluded"] == 2
    with_refs = graph_summary(_graph(), include_text_refs=True)
    assert any(e["file"] == "README.md" for e in with_refs["most_depended_on"])


def test_graph_summary_survives_a_chain_deeper_than_the_recursion_limit():
    chain = {f"m{i}.py": {"internal": [f"m{i + 1}.py"]} for i in range(3000)}
    chain["m3000.py"] = {"internal": ["m2999.py"]}  # a 2-cycle at the very end
    s = graph_summary(chain)
    assert s["cycles"] == [["m2999.py", "m3000.py"]]


def test_blast_radius_matches_the_naive_walk_and_respects_text_refs():
    g = _graph()
    assert get_blast_radius(g, "c.py", include_text_refs=False) == ["a.py", "b.py", "c.py", "pkg/d.py"]
    assert "README.md" in get_blast_radius(g, "c.py", include_text_refs=True)


def test_normalize_path_strips_leading_dot_slash_and_backslashes():
    assert normalize_path(".\\src\\a.py") == "src/a.py"
    assert normalize_path("././src/a.py") == "src/a.py"


def _write_pack(tmp_path):
    aif = {
        "project": {"name": "demo"},
        "files": {n: {"summary": "x"} for n in ["a.py", "b.py", "README.md"]},
        "relationships": {"a.py": {"internal": ["b.py"], "external": []}},
    }
    aif_path = tmp_path / "demo.json"
    aif_path.write_text(json.dumps(aif), encoding="utf-8")
    (tmp_path / "demo.detail.json").write_text(
        json.dumps({"README.md": {"compressed": "", "text_refs": ["a.py"]}}), encoding="utf-8"
    )
    return str(aif_path)


def test_get_graph_summary_via_query_service_scopes_to_code_files(tmp_path):
    aif_path = _write_pack(tmp_path)
    s = query_service.get_graph_summary(aif_path)
    assert s["file_count"] == 2 and s["edge_count"] == 1
    assert s["most_depended_on"] == [{"file": "b.py", "count": 1}]


def test_certain_edge_queries_never_read_detail_json(tmp_path):
    aif_path = _write_pack(tmp_path)
    (tmp_path / "demo.detail.json").write_text("{ corrupt", encoding="utf-8")
    assert query_service.get_dependents(aif_path, "b.py", include_text_refs=False) == ["a.py"]
    assert query_service.get_relationships(aif_path) == {"a.py": {"internal": ["b.py"], "external": []}}


def test_stale_check_rejects_a_project_path_that_is_not_a_directory(tmp_path):
    aif_path = _write_pack(tmp_path)
    (tmp_path / "demo.cache.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        query_service.get_overview(aif_path, str(tmp_path / "missing"))
    with pytest.raises(ValueError):
        query_service.check_freshness(str(tmp_path / "missing"), aif_path)


def test_stale_check_is_reused_within_its_ttl(tmp_path, monkeypatch):
    aif_path = _write_pack(tmp_path)
    (tmp_path / "demo.cache.json").write_text("{}", encoding="utf-8")
    calls = []
    real = query_service.check_freshness_scoped
    monkeypatch.setattr(query_service, "check_freshness_scoped", lambda *a, **k: calls.append(1) or real(*a, **k))
    query_service.list_files(aif_path, str(tmp_path), folder="src")
    query_service.list_files(aif_path, str(tmp_path), folder="pkg")
    assert len(calls) == 1


def test_skill_relationships_md_carries_the_graph_summary():
    aif = {"project": {"name": "demo"}, "files": {}, "relationships": _graph()}
    rel_md = generate_skill_files(aif, {})["references/relationships.md"]
    assert "## Graph summary" in rel_md
    assert "`b.py` <-> `c.py`" in rel_md
