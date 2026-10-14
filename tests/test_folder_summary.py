"""Covers folder_summary.py's grouping/generation logic in isolation from
the full pack() pipeline (see test_pack_integration.py for that, against
llm.MockProvider).
"""

import json

from ziplex import folder_summary, llm


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
    files_data = {"src/a.py": {"summary": "does a"}, "src/b.py": {"summary": "does b"}}

    assert folder_summary.generate_folder_summaries(files_data) == {"src": "Core application logic."}


def test_generate_folder_summaries_never_sends_a_one_file_folder_to_the_llm(monkeypatch):
    # packager._assemble_aif() drops one-file folders, so describing them
    # was paid output thrown away -- they get the free structural sentence.
    sent = []

    def _analyze(folders, lang="en"):
        sent.append(set(folders))
        return json.dumps({"src": "Core application logic."})

    monkeypatch.setattr(folder_summary, "analyze_folder_summaries", _analyze)
    files_data = {
        "src/a.py": {"summary": "does a"}, "src/b.py": {"summary": "does b"},
        "docs/guide.md": {"summary": "A guide."},
    }

    result = folder_summary.generate_folder_summaries(files_data)
    assert sent == [{"src"}]
    assert result == {"src": "Core application logic.", "docs": "Contains 1 file(s): guide.md"}


def test_generate_folder_summaries_makes_no_llm_call_when_every_folder_has_one_file(monkeypatch):
    # Counted, not raised: generate_folder_summaries()'s broad except would
    # swallow an AssertionError raised from inside the fake.
    calls = []
    monkeypatch.setattr(folder_summary, "analyze_folder_summaries", lambda *a, **k: calls.append(a) or "{}")
    result = folder_summary.generate_folder_summaries({"src/a.py": {"summary": "does a"}})
    assert calls == []
    assert result == {"src": "Contains 1 file(s): a.py"}


def test_generate_folder_summaries_falls_back_structurally_on_a_missing_key(monkeypatch):
    # The LLM response covers "src" but not "docs" -- "docs" must fall back
    # to a structural sentence rather than being dropped or left blank.
    monkeypatch.setattr(
        folder_summary, "analyze_folder_summaries",
        lambda folders, lang="en": json.dumps({"src": "Core application logic."}),
    )
    files_data = {
        "src/a.py": {"summary": "does a"}, "src/b.py": {"summary": "does b"},
        "docs/guide.md": {"summary": "A guide."}, "docs/faq.md": {"summary": "FAQ."},
    }

    result = folder_summary.generate_folder_summaries(files_data)
    assert result["src"] == "Core application logic."
    assert result["docs"] == "Contains 2 file(s): guide.md, faq.md"


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


def test_group_confidence_by_folder_averages_member_files():
    files_data = {
        "src/a.py": {"confidence": 1.0},
        "src/b.py": {"confidence": 0.0},
        "docs/guide.md": {"confidence": 0.67},
    }
    result = folder_summary.group_confidence_by_folder(files_data)

    assert result == {"src": 0.5, "docs": 0.67}


def test_group_confidence_by_folder_skips_files_with_no_confidence_yet():
    # Called before packager.py's per-file confidence loop has run, or on a
    # file that legitimately has none yet -- must not treat that as 0.
    files_data = {"src/a.py": {}, "src/b.py": {"confidence": 1.0}}
    assert folder_summary.group_confidence_by_folder(files_data) == {"src": 1.0}


def test_group_confidence_by_folder_returns_empty_dict_for_no_scored_files():
    assert folder_summary.group_confidence_by_folder({"src/a.py": {}}) == {}


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


def test_generate_folder_summaries_chunks_a_large_project_under_the_caps(monkeypatch):
    prompts = _recording_generate(monkeypatch)
    files_data = _big_project()

    result = folder_summary.generate_folder_summaries(files_data)

    assert len(prompts) > 1
    for prompt in prompts:
        assert len(prompt) <= folder_summary.FOLDER_PROMPT_MAX_CHARS
        assert prompt.count("\nFolder: ") <= folder_summary.FOLDER_CHUNK_MAX_FOLDERS
    assert set(result) == set(folder_summary.group_files_by_folder(files_data))
    assert all(summary == f"LLM: {folder}" for folder, summary in result.items())
    sent = [line for prompt in prompts for line in prompt.splitlines() if line.startswith("Folder: ")]
    assert len(sent) == len(set(sent)) == 600


def test_generate_folder_summaries_falls_back_per_failed_chunk_only(monkeypatch):
    _recording_generate(monkeypatch, fail_folder="pkg0/mod0")
    files_data = _big_project()

    result = folder_summary.generate_folder_summaries(files_data)

    first_chunk = folder_summary.chunk_folders(folder_summary.group_files_by_folder(files_data))[0]
    assert "pkg0/mod0" in first_chunk
    for folder, summary in result.items():
        if folder in first_chunk:
            assert summary.startswith("Contains 9 file(s)")
        else:
            assert summary == f"LLM: {folder}"


def test_generate_folder_summaries_is_deterministic(monkeypatch):
    _recording_generate(monkeypatch)
    files_data = _big_project(folders=200)
    first = folder_summary.generate_folder_summaries(files_data)
    second = folder_summary.generate_folder_summaries(files_data)
    assert first == second
    assert list(first) == list(folder_summary.group_files_by_folder(files_data))


def test_chunk_folders_keeps_order_and_respects_both_caps():
    folders = {f"f{i}": [f"a{i}.py: x", f"b{i}.py: y"] for i in range(100)}
    chunks = folder_summary.chunk_folders(folders)
    assert [folder for chunk in chunks for folder in chunk] == list(folders)
    assert all(len(chunk) <= folder_summary.FOLDER_CHUNK_MAX_FOLDERS for chunk in chunks)

    wide = {f"f{i}": [f"file{k}.py: " + "z" * 300 for k in range(25)] for i in range(20)}
    wide_chunks = folder_summary.chunk_folders(wide)
    assert len(wide_chunks) > 1
    for chunk in wide_chunks:
        joined = "\n\n".join(llm.format_folder_block(f, e) for f, e in chunk.items())
        assert len(joined) + llm.FOLDER_PROMPT_OVERHEAD_CHARS <= folder_summary.FOLDER_PROMPT_MAX_CHARS


def test_generate_folder_summaries_should_stop_skips_llm_calls(monkeypatch):
    prompts = _recording_generate(monkeypatch)
    files_data = _big_project(folders=100)

    result = folder_summary.generate_folder_summaries(files_data, should_stop=lambda: True)

    assert prompts == []
    assert all(summary.startswith("Contains 9 file(s)") for summary in result.values())
