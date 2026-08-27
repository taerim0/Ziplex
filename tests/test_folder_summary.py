"""Covers folder_summary.py's grouping/generation logic in isolation from
the full pack() pipeline (see test_pack_integration.py for that, against
llm.MockProvider).
"""

import json

from ziplex import folder_summary


def test_group_files_by_folder_groups_by_direct_parent_only():
    files_data = {
        "README.md": {"summary": "Project readme."},
        "src/main.py": {"summary": "Entry point."},
        "src/utils/helpers.py": {"summary": "Shared helpers."},
    }
    grouped = folder_summary.group_files_by_folder(files_data)

    assert grouped == {
        ".": ["README.md: Project readme."],
        "src": ["main.py: Entry point."],
        "src/utils": ["helpers.py: Shared helpers."],
    }


def test_group_files_by_folder_handles_a_file_with_no_summary():
    files_data = {"a.py": {"summary": ""}}
    assert folder_summary.group_files_by_folder(files_data) == {".": ["a.py"]}


def test_generate_structural_folder_summaries_lists_filenames():
    files_data = {
        "src/a.py": {"summary": "does a"},
        "src/b.py": {"summary": "does b"},
    }
    result = folder_summary.generate_structural_folder_summaries(files_data, lang="en")

    assert result == {"src": "Contains 2 file(s): a.py, b.py"}


def test_generate_structural_folder_summaries_caps_shown_filenames():
    files_data = {f"src/f{i}.py": {"summary": ""} for i in range(7)}
    result = folder_summary.generate_structural_folder_summaries(files_data, lang="en")

    assert result["src"] == "Contains 7 file(s): f0.py, f1.py, f2.py, f3.py, f4.py +2 more"


def test_generate_structural_folder_summaries_follows_lang():
    files_data = {"src/a.py": {"summary": ""}}
    result = folder_summary.generate_structural_folder_summaries(files_data, lang="ko")

    assert result == {"src": "파일 1개 포함: a.py"}


def test_generate_folder_summaries_uses_the_llm_response_when_complete(monkeypatch):
    monkeypatch.setattr(
        folder_summary, "analyze_folder_summaries",
        lambda folders, lang="en": json.dumps({"src": "Core application logic."}),
    )
    files_data = {"src/a.py": {"summary": "does a"}}

    assert folder_summary.generate_folder_summaries(files_data) == {"src": "Core application logic."}


def test_generate_folder_summaries_falls_back_structurally_on_a_missing_key(monkeypatch):
    # The LLM response covers "src" but not "docs" -- "docs" must fall back
    # to a structural sentence rather than being dropped or left blank.
    monkeypatch.setattr(
        folder_summary, "analyze_folder_summaries",
        lambda folders, lang="en": json.dumps({"src": "Core application logic."}),
    )
    files_data = {"src/a.py": {"summary": "does a"}, "docs/guide.md": {"summary": "A guide."}}

    result = folder_summary.generate_folder_summaries(files_data)
    assert result["src"] == "Core application logic."
    assert result["docs"] == "Contains 1 file(s): guide.md"


def test_generate_folder_summaries_falls_back_structurally_on_invalid_json(monkeypatch):
    monkeypatch.setattr(folder_summary, "analyze_folder_summaries", lambda folders, lang="en": "not json")
    files_data = {"src/a.py": {"summary": "does a"}}

    result = folder_summary.generate_folder_summaries(files_data)
    assert result == {"src": "Contains 1 file(s): a.py"}


def test_generate_folder_summaries_falls_back_structurally_on_any_exception(monkeypatch):
    # Regression for a real gap code review caught: the except clause used
    # to only catch json.JSONDecodeError, so anything else
    # analyze_folder_summaries() could raise (an unexpected response
    # shape, a provider raising instead of returning "{}") would abort the
    # whole pack() run instead of degrading -- with no checkpoint safety
    # net for this step at all, unlike rules/prompt.
    def _raises(folders, lang="en"):
        raise RuntimeError("unexpected provider failure")

    monkeypatch.setattr(folder_summary, "analyze_folder_summaries", _raises)
    files_data = {"src/a.py": {"summary": "does a"}}

    result = folder_summary.generate_folder_summaries(files_data)
    assert result == {"src": "Contains 1 file(s): a.py"}


def test_generate_folder_summaries_returns_empty_dict_for_no_files(monkeypatch):
    def _unexpected_call(*a, **k):
        raise AssertionError("must not call the LLM when there are no files at all")

    monkeypatch.setattr(folder_summary, "analyze_folder_summaries", _unexpected_call)
    assert folder_summary.generate_folder_summaries({}) == {}
