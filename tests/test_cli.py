import json
import sys

import pytest

from ziplex import cli
from ziplex import llm
from ziplex import summarizer
from ziplex.cli import _split_patterns, _check_max_tokens
from ziplex.freshness import build_manifest


def test_split_patterns_none_when_no_value():
    assert _split_patterns(None) is None
    assert _split_patterns("") is None


def test_split_patterns_splits_on_comma():
    assert _split_patterns("src/**/*.py,*.md") == ["src/**/*.py", "*.md"]


def test_split_patterns_strips_whitespace_around_each_pattern():
    # "src/**/*.py, *.md" (a space after the comma) used to leave " *.md"
    # as a literal leading-space pattern that pathspec matches nothing
    # against, silently dropping every intended file.
    assert _split_patterns("src/**/*.py, *.md , other/**") == ["src/**/*.py", "*.md", "other/**"]


def test_split_patterns_drops_empty_entries():
    assert _split_patterns("a.py,,b.py,") == ["a.py", "b.py"]


def _tokens(compressed: int) -> dict:
    return {"GPT-4o": {"original": 9999, "compressed": compressed, "saved_pct": 0.0}}


def test_check_max_tokens_passes_when_under_budget():
    passed, actual = _check_max_tokens(_tokens(100), max_tokens=200, model="GPT-4o")
    assert passed is True
    assert actual == 100


def test_check_max_tokens_passes_when_exactly_at_budget():
    passed, actual = _check_max_tokens(_tokens(200), max_tokens=200, model="GPT-4o")
    assert passed is True
    assert actual == 200


def test_check_max_tokens_fails_when_over_budget():
    passed, actual = _check_max_tokens(_tokens(300), max_tokens=200, model="GPT-4o")
    assert passed is False
    assert actual == 300


def test_check_max_tokens_returns_none_for_unknown_model():
    passed, actual = _check_max_tokens(_tokens(100), max_tokens=200, model="Not-A-Real-Model")
    assert passed is False
    assert actual is None


def test_pack_main_fails_loudly_when_max_tokens_requested_but_pack_never_completed(tmp_path, monkeypatch):
    # pack() returns {} on a checkpoint-and-exit (a repeated LLM failure) or
    # a cancelled/empty run -- the whole --max-tokens guard block used to
    # sit inside `if aif:` with no else, so main() just exited 0 in that
    # case: exactly the scenario the flag exists to catch (a CI pipeline
    # silently passing despite pack() never actually completing).
    monkeypatch.setattr(cli, "pack", lambda *a, **k: {})
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "pack", str(tmp_path), "--auto", "--auto-correct", "--max-tokens", "1000"],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1


def test_pack_main_exits_cleanly_when_pack_incomplete_and_no_max_tokens_requested(tmp_path, monkeypatch):
    # Without --max-tokens, an incomplete pack() (checkpoint-and-exit, or
    # nothing selected) must behave exactly as before this fix -- no error
    # message, no non-zero exit, since nothing was ever asked to be
    # verified.
    monkeypatch.setattr(cli, "pack", lambda *a, **k: {})
    monkeypatch.setattr(sys, "argv", ["cli.py", "pack", str(tmp_path), "--auto", "--auto-correct"])

    cli.main()  # must not raise SystemExit


def test_freshness_main_exits_nonzero_when_stale(tmp_path, monkeypatch, capsys):
    # `ziplex freshness` doubles as a free CI/PR gate (no LLM calls, just a
    # hash comparison) -- it must fail loudly (non-zero exit) when the
    # committed cache.json has drifted from disk, or a CI pipeline wired to
    # it would silently pass on a stale, un-re-reviewed aif.json forever.
    project = tmp_path / "project"
    project.mkdir()
    file_path = project / "main.py"
    file_path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    manifest = build_manifest([str(file_path)], str(project))
    cache_path = tmp_path / "out.cache.json"
    cache_path.write_text(json.dumps(manifest), encoding="utf-8")

    file_path.write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["cli.py", "freshness", str(project), str(cache_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert "오래됐습니다" in capsys.readouterr().out


def test_freshness_main_exits_cleanly_when_unchanged(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    file_path = project / "main.py"
    file_path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    manifest = build_manifest([str(file_path)], str(project))
    cache_path = tmp_path / "out.cache.json"
    cache_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["cli.py", "freshness", str(project), str(cache_path)])

    cli.main()  # must not raise SystemExit

    assert "최신 상태" in capsys.readouterr().out


def test_analyze_command_delegates_to_summarizer_and_shares_its_failure_placeholder(tmp_path, monkeypatch, capsys):
    # analyze used to call llm.analyze_file_summary() directly in its own
    # bespoke per-file loop -- no batching, no shared retry-once-then-
    # placeholder logic, and its own separate failure string ("분석 실패")
    # instead of summarizer.SUMMARY_FAILED_PLACEHOLDER ("요약 생성 실패"),
    # the one confidence.py specifically recognizes. Refactored to delegate
    # to summarizer.generate_summaries() -- the same path pack() itself
    # uses -- so a future fix there (batching, retry, placeholder handling)
    # no longer silently misses this command.
    class _FailingProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5) -> str:
            return "not valid json"

    monkeypatch.setattr(llm, "_provider", _FailingProvider())

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["cli.py", "analyze", str(project)])
    cli.main()

    out = capsys.readouterr().out
    assert summarizer.SUMMARY_FAILED_PLACEHOLDER in out
    assert "분석 실패" not in out
