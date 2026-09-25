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
        lambda items, lang="en": json.dumps({"summaries": {"a.py": "does a", "b.py": "does b"}}),
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
        lambda items, lang="en": json.dumps({"summaries": {"a.py": "does a"}}),
    )
    monkeypatch.setattr(summarizer, "request_summary", lambda name, data, lang="en": f"fallback for {name}")

    batch = [("a.py", {"signatures": [], "dependencies": []}), ("b.py", {"signatures": [], "dependencies": []})]
    assert summarizer.request_batch_summaries(batch) == {"a.py": "does a", "b.py": "fallback for b.py"}


def test_request_batch_summaries_falls_back_entirely_on_a_garbled_response(monkeypatch):
    monkeypatch.setattr(summarizer, "analyze_batch_summaries", lambda items, lang="en": "not json")
    monkeypatch.setattr(summarizer, "request_summary", lambda name, data, lang="en": f"fallback for {name}")

    batch = [("a.py", {"signatures": [], "dependencies": []})]
    assert summarizer.request_batch_summaries(batch) == {"a.py": "fallback for a.py"}


def _batch(*names):
    return [(n, {"signatures": [], "dependencies": []}) for n in names]


def test_request_batch_summaries_rebatches_misses_once_before_going_per_file(monkeypatch):
    # First call garbled, second call (only the missed names) succeeds --
    # 2 requests total instead of 1 + one per file.
    calls = []

    def _analyze(items, lang="en"):
        calls.append([i["file"] for i in items])
        if len(calls) == 1:
            return "not json"
        return json.dumps({"summaries": {i["file"]: f"does {i['file']}" for i in items}})

    monkeypatch.setattr(summarizer, "analyze_batch_summaries", _analyze)

    def _unexpected_fallback(*a, **k):
        raise AssertionError("per-file fallback must not run when the re-batch covers everything")

    monkeypatch.setattr(summarizer, "request_summary", _unexpected_fallback)

    result = summarizer.request_batch_summaries(_batch("a.py", "b.py", "c.py"))
    assert result == {"a.py": "does a.py", "b.py": "does b.py", "c.py": "does c.py"}
    assert calls == [["a.py", "b.py", "c.py"], ["a.py", "b.py", "c.py"]]


def test_request_batch_summaries_goes_per_file_only_for_what_the_rebatch_still_misses(monkeypatch):
    calls = []

    def _analyze(items, lang="en"):
        calls.append(len(items))
        return json.dumps({"summaries": {"a.py": "does a"}})

    monkeypatch.setattr(summarizer, "analyze_batch_summaries", _analyze)
    monkeypatch.setattr(summarizer, "request_summary", lambda name, data, lang="en": f"fallback for {name}")

    result = summarizer.request_batch_summaries(_batch("a.py", "b.py", "c.py"))
    assert result == {"a.py": "does a", "b.py": "fallback for b.py", "c.py": "fallback for c.py"}
    assert calls == [3, 2]  # never re-batched a second time


def test_request_batch_summaries_skips_the_rebatch_when_the_provider_gave_up(monkeypatch):
    # "{}" is llm._retry_loop()'s give-up sentinel (e.g. rate limit after
    # every retry) -- repeating the batch would only add load, not recover.
    calls = []

    def _analyze(items, lang="en"):
        calls.append(len(items))
        return "{}"

    monkeypatch.setattr(summarizer, "analyze_batch_summaries", _analyze)
    monkeypatch.setattr(summarizer, "request_summary", lambda name, data, lang="en": f"fallback for {name}")

    result = summarizer.request_batch_summaries(_batch("a.py", "b.py"))
    assert result == {"a.py": "fallback for a.py", "b.py": "fallback for b.py"}
    assert calls == [2]


def test_request_batch_summaries_matches_keys_echoed_with_path_noise(monkeypatch):
    monkeypatch.setattr(
        summarizer, "analyze_batch_summaries",
        lambda items, lang="en": json.dumps({"summaries": {"./src/a.py": "does a", " src\\b.py ": "does b"}}),
    )

    def _unexpected_fallback(*a, **k):
        raise AssertionError("a normalized key match must not cost a fallback request")

    monkeypatch.setattr(summarizer, "request_summary", _unexpected_fallback)

    assert summarizer.request_batch_summaries(_batch("src/a.py", "src/b.py")) == {
        "src/a.py": "does a", "src/b.py": "does b",
    }


def test_parse_batch_response_accepts_the_shapes_models_actually_return():
    expected = {"a.py": "does a", "b.py": "does b"}
    shapes = [
        {"summaries": {"a.py": "does a", "b.py": "does b"}},
        {"summaries": [{"file": "a.py", "summary": "does a"}, {"file": "b.py", "summary": "does b"}]},
        {"a.py": "does a", "b.py": "does b"},
        {"summaries": {"a.py": {"summary": "does a"}, "b.py": {"summary": "does b"}}},
    ]
    for shape in shapes:
        assert summarizer._parse_batch_response(json.dumps(shape)) == expected


def test_parse_batch_response_never_raises_on_non_object_json():
    for response in ("[1, 2]", '"text"', "42", "null", "not json"):
        assert summarizer._parse_batch_response(response) == {}


def test_request_summary_returns_empty_on_non_object_json(monkeypatch):
    # Used to raise AttributeError (list has no .get) inside the thread pool.
    monkeypatch.setattr(summarizer, "analyze_text_summary", lambda *a, **k: '["not", "an", "object"]')
    assert summarizer.request_summary("a.md", {"signatures": [], "dependencies": [], "compressed": "x"}) == ""


def test_generate_summaries_returns_a_summary_per_pending_file_keyed_by_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        summarizer, "analyze_batch_summaries",
        lambda items, lang="en": json.dumps({"summaries": {"a.py": "does a"}}),
    )

    root = tmp_path / "project"
    root.mkdir()
    fp = str(root / "a.py")
    pending = {fp: {"signatures": [], "dependencies": []}}

    assert summarizer.generate_summaries(pending, root) == {fp: "does a"}
    # A real summary is logged as a success, not a failure.
    out = capsys.readouterr().out
    assert "✅ a.py" in out
    assert "❌" not in out


def test_generate_summaries_placeholders_a_summary_that_never_comes_back(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(summarizer, "analyze_batch_summaries", lambda items, lang="en": json.dumps({"summaries": {}}))
    monkeypatch.setattr(summarizer, "request_summary", lambda name, data, lang="en": "")

    root = tmp_path / "project"
    root.mkdir()
    fp = str(root / "a.py")
    pending = {fp: {"signatures": [], "dependencies": []}}

    assert summarizer.generate_summaries(pending, root) == {fp: summarizer.SUMMARY_FAILED_PLACEHOLDERS["en"]}
    # Real bug reported directly: a placeholder-substituted summary used to
    # print "✅" unconditionally, indistinguishable from a real success --
    # nothing in the log pointed a human at the one file correct_aif()'s
    # review is actually supposed to catch.
    out = capsys.readouterr().out
    assert "❌ a.py" in out
    assert "✅" not in out


def test_generate_summaries_placeholders_in_the_requested_language(tmp_path, monkeypatch):
    monkeypatch.setattr(summarizer, "analyze_batch_summaries", lambda items, lang="en": json.dumps({"summaries": {}}))
    monkeypatch.setattr(summarizer, "request_summary", lambda name, data, lang="en": "")

    root = tmp_path / "project"
    root.mkdir()
    fp = str(root / "a.py")
    pending = {fp: {"signatures": [], "dependencies": []}}

    assert summarizer.generate_summaries(pending, root, lang="ko") == {fp: summarizer.SUMMARY_FAILED_PLACEHOLDERS["ko"]}


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


def test_structural_summary_localizes_labels_for_korean():
    data = {"signatures": ["add(a, b)"], "dependencies": []}
    assert summarizer._structural_summary(data, lang="ko") == "정의: add(a, b)"

    data = {"signatures": [], "dependencies": ["os"]}
    assert summarizer._structural_summary(data, lang="ko") == "참조: os"

    data = {"signatures": [], "dependencies": []}
    assert summarizer._structural_summary(data, lang="ko") == (
        "감지된 시그니처/의존성 없음 (구조 정보 전용 모드, LLM 요약 없음)."
    )


def test_is_summary_failed_placeholder_recognizes_every_supported_language():
    assert summarizer.is_summary_failed_placeholder(summarizer.SUMMARY_FAILED_PLACEHOLDERS["en"])
    assert summarizer.is_summary_failed_placeholder(summarizer.SUMMARY_FAILED_PLACEHOLDERS["ko"])
    assert not summarizer.is_summary_failed_placeholder("a real summary")


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
