"""Regression coverage for query_service.py respecting a project's own
.ziplex.json (config.py) the same way packager.pack() itself does --
before this, check_freshness/_stale_warning/search_project all re-collected
a project's *unscoped* full file tree, disagreeing with what an
include/ignore-scoped pack() actually produced.
"""
import json

from ziplex import checkpoint
from ziplex import llm
from ziplex import packager
from ziplex import query_service


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_check_freshness_does_not_flag_ziplex_json_excluded_files_as_added(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "src" / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "docs" / "notes.md", "# excluded from packing\n")
    _write(project / ".ziplex.json", json.dumps({"include": ["src/**"], "ignore": []}))

    aif = packager.pack(str(project), auto=True, interactive=False)
    aif_path = tmp_path / "out.json"
    packager.save_aif(aif, str(aif_path))

    report = query_service.check_freshness(str(project), str(aif_path))

    # docs/notes.md was never part of the pack (excluded by include) -- it
    # must not show up as "added" just because it exists on disk unscoped.
    assert report["is_stale"] is False
    assert report["added"] == []
    assert report["changed"] == []
    assert report["removed"] == []


def test_stale_warning_is_none_for_an_unchanged_scoped_project(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "src" / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "docs" / "notes.md", "# excluded from packing\n")
    _write(project / ".ziplex.json", json.dumps({"include": ["src/**"], "ignore": []}))

    aif = packager.pack(str(project), auto=True, interactive=False)
    aif_path = tmp_path / "out.json"
    packager.save_aif(aif, str(aif_path))

    overview = query_service.get_overview(str(aif_path), str(project))
    assert "_stale" not in overview


def test_search_project_does_not_search_ziplex_json_ignored_files(tmp_path):
    project = tmp_path / "project"
    _write(project / "src" / "main.py", "TARGET_TOKEN = 1\n")
    _write(project / "vendor" / "lib.py", "TARGET_TOKEN = 2\n")
    _write(project / ".ziplex.json", json.dumps({"include": [], "ignore": ["vendor/**"]}))

    results = query_service.search_project(str(project), "TARGET_TOKEN")

    files_matched = {r["file"] for r in results}
    assert any("main.py" in f for f in files_matched)
    assert not any("lib.py" in f for f in files_matched)
