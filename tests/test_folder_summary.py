"""Covers folder_summary.py's grouping/generation logic in isolation from
the full pack() pipeline (see test_pack_integration.py for that, against
llm.MockProvider).
"""

import json

from ziplex import folder_summary
from ziplex.enrich import llm


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


def _big_project(folders=600, files_per_folder=9):
    return {
        f"pkg{f // 30}/mod{f}/file_{f}_{i}.py": {
            "summary": f"Handles widget request processing number {i} for module {f} via handle_widget_request.",
        }
        for f in range(folders)
        for i in range(files_per_folder)
    }


def _recording_generate(monkeypatch, fail_folder=None):
    """Fake llm.generate(): records each real folder prompt and answers
    every folder in it (or returns bad JSON for the chunk holding
    `fail_folder`)."""
    prompts = []

    def fake_generate(prompt, retry=5, label=""):
        prompts.append(prompt)
        folders = [line[len("Folder: "):] for line in prompt.splitlines() if line.startswith("Folder: ")]
        if fail_folder in folders:
            return "not json"
        return json.dumps({folder: f"LLM: {folder}" for folder in folders})

    monkeypatch.setattr(llm, "generate", fake_generate)
    return prompts


