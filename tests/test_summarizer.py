"""Covers summarizer.py's batching/fallback logic in isolation from the full
pack() pipeline (see test_pack_integration.py for that, against
llm.MockProvider).
"""

import json

from ziplex import summarizer


def test_chunked_splits_into_groups_of_size():
    assert summarizer.chunked(["a", "b", "c", "d", "e"], 2) == [["a", "b"], ["c", "d"], ["e"]]


def test_request_batch_summaries_uses_the_batch_response_when_complete(monkeypatch):
    monkeypatch.setattr(
        summarizer, "analyze_batch_summaries",
        lambda items: json.dumps({"summaries": {"a.py": "does a", "b.py": "does b"}}),
    )

    def _unexpected_fallback(*a, **k):
        raise AssertionError("request_summary must not be called when the batch response is complete")

    monkeypatch.setattr(summarizer, "request_summary", _unexpected_fallback)

    batch = [("a.py", {"signatures": [], "dependencies": []}), ("b.py", {"signatures": [], "dependencies": []})]
    assert summarizer.request_batch_summaries(batch) == {"a.py": "does a", "b.py": "does b"}


def test_request_batch_summaries_falls_back_per_file_on_a_missing_key(monkeypatch):
    # the batch response only covers a.py -- b.py must fall back
    # individually rather than the whole batch being lost
    monkeypatch.setattr(
        summarizer, "analyze_batch_summaries",
        lambda items: json.dumps({"summaries": {"a.py": "does a"}}),
    )
    monkeypatch.setattr(summarizer, "request_summary", lambda name, data: f"fallback for {name}")

    batch = [("a.py", {"signatures": [], "dependencies": []}), ("b.py", {"signatures": [], "dependencies": []})]
    assert summarizer.request_batch_summaries(batch) == {"a.py": "does a", "b.py": "fallback for b.py"}


def test_request_batch_summaries_falls_back_entirely_on_a_garbled_response(monkeypatch):
    monkeypatch.setattr(summarizer, "analyze_batch_summaries", lambda items: "not json")
    monkeypatch.setattr(summarizer, "request_summary", lambda name, data: f"fallback for {name}")

    batch = [("a.py", {"signatures": [], "dependencies": []})]
    assert summarizer.request_batch_summaries(batch) == {"a.py": "fallback for a.py"}


def test_generate_summaries_returns_a_summary_per_pending_file_keyed_by_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        summarizer, "analyze_batch_summaries",
        lambda items: json.dumps({"summaries": {"a.py": "does a"}}),
    )

    root = tmp_path / "project"
    root.mkdir()
    fp = str(root / "a.py")
    pending = {fp: {"signatures": [], "dependencies": []}}

    assert summarizer.generate_summaries(pending, root) == {fp: "does a"}


def test_generate_summaries_placeholders_a_summary_that_never_comes_back(tmp_path, monkeypatch):
    monkeypatch.setattr(summarizer, "analyze_batch_summaries", lambda items: json.dumps({"summaries": {}}))
    monkeypatch.setattr(summarizer, "request_summary", lambda name, data: "")

    root = tmp_path / "project"
    root.mkdir()
    fp = str(root / "a.py")
    pending = {fp: {"signatures": [], "dependencies": []}}

    assert summarizer.generate_summaries(pending, root) == {fp: "요약 생성 실패"}


def test_structural_summary_lists_signatures_when_present():
    data = {"signatures": ["add(a, b)", "sub(a, b)"], "dependencies": ["os"]}
    # signatures take priority over dependencies when both exist -- they're
    # the more specific fact
    assert summarizer._structural_summary(data) == "Defines: add(a, b), sub(a, b)"


def test_structural_summary_caps_and_counts_extra_signatures():
    sigs = [f"fn{i}()" for i in range(8)]
    result = summarizer._structural_summary({"signatures": sigs, "dependencies": []})
    assert result == "Defines: fn0(), fn1(), fn2(), fn3(), fn4(), +3 more"


def test_structural_summary_falls_back_to_dependencies_when_no_signatures():
    data = {"signatures": [], "dependencies": ["flask", "os"]}
    assert summarizer._structural_summary(data) == "References: flask, os"


def test_structural_summary_falls_back_to_a_fixed_note_when_neither_exists():
    data = {"signatures": [], "dependencies": []}
    assert summarizer._structural_summary(data) == (
        "No signatures or dependencies detected (structural-only mode, no LLM summary)."
    )


def test_generate_structural_summaries_returns_one_summary_per_pending_file(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    fp_a = str(root / "a.py")
    fp_b = str(root / "b.py")
    pending = {
        fp_a: {"signatures": ["add()"], "dependencies": []},
        fp_b: {"signatures": [], "dependencies": []},
    }

    result = summarizer.generate_structural_summaries(pending, root)

    assert result == {
        fp_a: "Defines: add()",
        fp_b: "No signatures or dependencies detected (structural-only mode, no LLM summary).",
    }
