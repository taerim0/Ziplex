"""Regression coverage for query_service.py respecting a project's own
.ziplex.json (config.py) the same way packager.pack() itself does --
before this, check_freshness/_stale_warning/search_project all re-collected
a project's *unscoped* full file tree, disagreeing with what an
include/ignore-scoped pack() actually produced.
"""
import json

import pytest

from ziplex import checkpoint
from ziplex import llm
from ziplex import packager
from ziplex import query_service
from ziplex.confidence import REVIEW_THRESHOLD


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


def test_check_freshness_does_not_flag_a_previously_included_dangerous_file_as_removed(tmp_path, monkeypatch):
    # Real bug reported directly: a file flagged sensitive by scan_files()
    # but included in the pack anyway (here via `preselected`, the GUI's
    # own mechanism -- see file/AGENTS.md's selector.py section) gets
    # re-flagged as dangerous on every later scan regardless of that
    # earlier decision, dropped from collect_and_scan()'s own "safe" list
    # every time -- check_freshness() used to only ever look at "safe", so
    # it reported this exact file as permanently removed even though it's
    # unchanged and still on disk.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "config.py", 'API_KEY = "abc123"\n')

    aif = packager.pack(str(project), preselected=["config.py"], interactive=False)
    aif_path = tmp_path / "out.json"
    packager.save_aif(aif, str(aif_path))

    report = query_service.check_freshness(str(project), str(aif_path))

    assert report["is_stale"] is False
    assert report["removed"] == []
    assert report["unchanged_count"] == 1


def test_get_overview_stale_field_does_not_flag_a_previously_included_dangerous_file(tmp_path, monkeypatch):
    # Same bug as the standalone check_freshness() test above, but for
    # _stale_warning() -- a *separate* private helper with its own inline
    # collect_and_scan()["safe"] call, missed in the first pass at this fix
    # since it lives in a different function than the one the bug was
    # originally reported against. Symptom that made the miss visible:
    # opening a project in the GUI showed "changed" for the previously-
    # included file for about a second, until the page's own live watcher
    # (gui/watcher.py, fixed correctly the first time) caught up and
    # corrected the badge -- get_overview()'s own "_stale" field is what
    # the *initial* page load actually shows before that correction lands.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "config.py", 'API_KEY = "abc123"\n')

    aif = packager.pack(str(project), preselected=["config.py"], interactive=False)
    aif_path = tmp_path / "out.json"
    packager.save_aif(aif, str(aif_path))

    result = query_service.get_overview(str(aif_path), str(project))

    assert "_stale" not in result


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


def test_get_folders_reads_the_folders_field_from_aif_json(tmp_path):
    aif_path = tmp_path / "out.json"
    aif_path.write_text(
        json.dumps({"project": {}, "files": {}, "folders": {"src": {"summary": "Core logic."}}}),
        encoding="utf-8",
    )

    assert query_service.get_folders(str(aif_path)) == {"src": {"summary": "Core logic."}}


def test_get_folders_returns_empty_dict_for_an_aif_json_packed_before_this_field_existed(tmp_path):
    aif_path = tmp_path / "out.json"
    aif_path.write_text(json.dumps({"project": {}, "files": {}}), encoding="utf-8")

    assert query_service.get_folders(str(aif_path)) == {}


def test_search_project_does_not_search_ziplex_json_ignored_files(tmp_path):
    project = tmp_path / "project"
    _write(project / "src" / "main.py", "TARGET_TOKEN = 1\n")
    _write(project / "vendor" / "lib.py", "TARGET_TOKEN = 2\n")
    _write(project / ".ziplex.json", json.dumps({"include": [], "ignore": ["vendor/**"]}))

    result = query_service.search_project(str(project), "TARGET_TOKEN")

    files_matched = {r["file"] for r in result["matches"]}
    assert any("main.py" in f for f in files_matched)
    assert not any("lib.py" in f for f in files_matched)
    assert result["truncated"] is False


def test_search_project_caps_results_and_reports_truncation(tmp_path):
    # A broad/common pattern against a real project can return far more
    # matches than any caller actually wants in one response -- measured
    # directly against a real 47-file project: 204 matches, ~9,000 tokens,
    # for a single common word with no cap at all.
    project = tmp_path / "project"
    _write(project / "a.py", "MATCH\n" * 10)

    capped = query_service.search_project(str(project), "MATCH", max_results=3)
    assert len(capped["matches"]) == 3
    assert capped["truncated"] is True

    uncapped = query_service.search_project(str(project), "MATCH", max_results=None)
    assert len(uncapped["matches"]) == 10
    assert uncapped["truncated"] is False


def _write_mixed_confidence_aif(tmp_path):
    aif_path = tmp_path / "out.json"
    aif_path.write_text(json.dumps({
        "project": {}, "files": {
            "src/a.py": {"summary": "high confidence", "confidence": 0.9},
            "src/b.py": {"summary": "low confidence", "confidence": 0.1},
            "docs/readme.md": {"summary": "root-adjacent doc", "confidence": 1.0},
            "top.py": {"summary": "root-level file", "confidence": 1.0},
        },
        "relationships": {
            "src/a.py": {"internal": [], "external": []},
            "src/b.py": {"internal": ["src/a.py"], "external": []},
            "docs/readme.md": {"internal": [], "external": []},
            "top.py": {"internal": [], "external": []},
        },
    }), encoding="utf-8")
    return str(aif_path)


def test_list_files_folder_filter_scopes_to_files_directly_inside_it(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.list_files(aif_path, folder="src")

    assert set(result) == {"src/a.py", "src/b.py"}


def test_list_files_folder_filter_dot_matches_root_level_files(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.list_files(aif_path, folder=".")

    assert set(result) == {"top.py"}


def test_list_files_folder_filter_normalizes_trailing_slash_and_empty_string(tmp_path):
    # A real rough edge caught in code review: parent_folder() never
    # produces "src/" or "" itself, so an un-normalized comparison would
    # silently return {} for either instead of matching -- "src/" should
    # behave exactly like "src", and "" (a plausible "give me root" guess)
    # should behave exactly like the real root sentinel ".".
    aif_path = _write_mixed_confidence_aif(tmp_path)

    assert set(query_service.list_files(aif_path, folder="src/")) == {"src/a.py", "src/b.py"}
    assert set(query_service.list_files(aif_path, folder="")) == {"top.py"}


def test_list_files_confidence_below_filters_by_the_given_cutoff(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.list_files(aif_path, confidence_below=REVIEW_THRESHOLD)

    assert set(result) == {"src/b.py"}


def test_list_files_confidence_below_accepts_a_custom_cutoff(tmp_path):
    # Not hardcoded to REVIEW_THRESHOLD -- a caller can ask for a stricter
    # or looser cutoff, e.g. "show me everything under 0.95".
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.list_files(aif_path, confidence_below=0.95)

    assert set(result) == {"src/a.py", "src/b.py"}


def test_list_files_folder_and_confidence_filters_compose(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.list_files(aif_path, folder="src", confidence_below=REVIEW_THRESHOLD)

    assert set(result) == {"src/b.py"}


def test_list_files_default_is_unfiltered(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.list_files(aif_path)

    assert set(result) == {"src/a.py", "src/b.py", "docs/readme.md", "top.py"}


def test_get_relationships_scopes_to_the_given_files(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.get_relationships(aif_path, files=["src/b.py"])

    assert result == {"src/b.py": {"internal": ["src/a.py"], "external": []}}


def test_get_relationships_silently_skips_a_name_not_in_the_graph(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.get_relationships(aif_path, files=["src/b.py", "nope.py"])

    assert result == {"src/b.py": {"internal": ["src/a.py"], "external": []}}


def test_get_relationships_default_is_the_whole_graph(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.get_relationships(aif_path)

    assert set(result) == {"src/a.py", "src/b.py", "docs/readme.md", "top.py"}


# Real bug found via a live MCP stdio call: get_dependents()/get_blast_radius()
# used to return an empty list for both a Windows-style backslash path and a
# genuine typo, indistinguishable from "this file really has zero
# dependents" -- the single worst wrong answer these tools could give an
# agent deciding whether a change is safe. get_detail() already raised the
# equivalent ValueError for an unrecognized file; these two didn't, purely by
# oversight. See query_service._require_known_file()'s own docstring.
def test_get_dependents_raises_on_a_file_not_in_the_graph(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    with pytest.raises(ValueError, match="src/nope.py"):
        query_service.get_dependents(aif_path, "src/nope.py")


def test_get_blast_radius_raises_on_a_file_not_in_the_graph(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    with pytest.raises(ValueError, match="src/nope.py"):
        query_service.get_blast_radius(aif_path, "src/nope.py")


def test_get_dependents_normalizes_a_backslash_path_before_matching(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.get_dependents(aif_path, "src\\a.py")

    assert result == ["src/b.py"]


def test_get_blast_radius_normalizes_a_backslash_path_before_matching(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.get_blast_radius(aif_path, "src\\a.py")

    assert result == ["src/b.py"]


def test_get_dependents_still_returns_an_empty_list_for_a_real_leaf_file(tmp_path):
    # The other half of the fix: a *recognized* file with genuinely zero
    # dependents must still return [] cleanly, not raise -- only an
    # unrecognized name is an error.
    aif_path = _write_mixed_confidence_aif(tmp_path)

    assert query_service.get_dependents(aif_path, "top.py") == []


def test_list_files_folder_filter_normalizes_a_backslash_path(tmp_path):
    aif_path = _write_mixed_confidence_aif(tmp_path)

    result = query_service.list_files(aif_path, folder="src\\")

    assert set(result) == {"src/a.py", "src/b.py"}


# Real gap found by code review: .ziplex.json's own scope was already
# respected (see the ignored-files test above), but a pack-specific
# --include/--ignore CLI extra -- not reproducible from disk the way
# .ziplex.json is, recorded only in that pack's own aif.json project.scope
# -- was silently ignored here, unlike check_freshness() which already read
# it back via load_pack_scope().
def test_search_project_ignores_a_packs_cli_only_ignore_extra_without_aif_path(tmp_path):
    # Without aif_path (the pre-existing behavior), the excluded file still
    # turns up.
    project = tmp_path / "project"
    _write(project / "src" / "main.py", "TARGET_TOKEN = 1\n")
    _write(project / "vendor" / "lib.py", "TARGET_TOKEN = 2\n")

    result = query_service.search_project(str(project), "TARGET_TOKEN")

    files_matched = {r["file"] for r in result["matches"]}
    assert any("lib.py" in f for f in files_matched)


def test_search_project_respects_a_packs_cli_only_ignore_extra_via_aif_path(tmp_path):
    project = tmp_path / "project"
    _write(project / "src" / "main.py", "TARGET_TOKEN = 1\n")
    _write(project / "vendor" / "lib.py", "TARGET_TOKEN = 2\n")
    aif_path = tmp_path / "out.json"
    _write(aif_path, json.dumps({"project": {"scope": {"include": [], "ignore": ["vendor/**"]}}}))

    result = query_service.search_project(str(project), "TARGET_TOKEN", aif_path=str(aif_path))

    files_matched = {r["file"] for r in result["matches"]}
    assert any("main.py" in f for f in files_matched)
    assert not any("lib.py" in f for f in files_matched)


# Real gap found via a live MCP stdio call: a bad aif_path used to leak
# Python's raw FileNotFoundError text ("[Errno 2] No such file or
# directory: '...'") straight through as the tool's error content -- no
# more actionable to a calling agent than a raw traceback would be to a
# human. cli.py got the equivalent fix (_load_json_or_exit()) well before
# this shared layer did.
def test_get_overview_missing_aif_path_raises_an_actionable_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="ziplex pack"):
        query_service.get_overview(str(tmp_path / "does_not_exist.json"))


def test_get_overview_corrupt_json_raises_an_actionable_message(tmp_path):
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError, match="corrupted"):
        query_service.get_overview(str(bad))


def test_search_project_default_cap_is_not_unlimited(tmp_path):
    project = tmp_path / "project"
    _write(project / "a.py", "MATCH\n" * (query_service.DEFAULT_SEARCH_MAX_RESULTS + 10))

    result = query_service.search_project(str(project), "MATCH")

    assert len(result["matches"]) == query_service.DEFAULT_SEARCH_MAX_RESULTS
    assert result["truncated"] is True
