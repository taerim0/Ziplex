import json
import sys

import pytest

from ziplex import checkpoint as app_checkpoint
from ziplex import cli
from ziplex import llm
from ziplex import settings as app_settings
from ziplex import summarizer
from ziplex.cli import _split_patterns, _check_max_tokens, _mask_secret
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


def test_version_flag_prints_version_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cli.py", "--version"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 0
    assert cli.__version__ in capsys.readouterr().out


def test_mask_secret_keeps_only_last_four_characters():
    assert _mask_secret("sk-ABCDEFGHIJKL1234") == "***************1234"


def test_mask_secret_masks_a_short_value_in_full():
    assert _mask_secret("abcd") == "****"
    assert _mask_secret("") == ""


def test_settings_get_prints_unset_hints_when_nothing_configured(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(sys, "argv", ["cli.py", "settings"])

    cli.main()  # must not raise SystemExit

    out = capsys.readouterr().out
    assert "gemini_api_key" in out
    assert "미설정" in out


def test_settings_get_hints_use_llm_py_own_default_constants(tmp_path, monkeypatch, capsys):
    # code-review finding: the unset-field hints must read llm.py's real
    # DEFAULT_MODEL/DEFAULT_BASE_URL/DEFAULT_PROVIDER_NAME, not a
    # hand-typed copy that can silently go stale when a default changes.
    from ziplex import llm

    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(sys, "argv", ["cli.py", "settings"])

    cli.main()

    out = capsys.readouterr().out
    assert llm.GeminiProvider.DEFAULT_MODEL in out
    assert llm.OpenAIProvider.DEFAULT_MODEL in out
    assert llm.OpenAIProvider.DEFAULT_BASE_URL in out
    assert llm.ClaudeProvider.DEFAULT_MODEL in out
    assert llm.DEFAULT_PROVIDER_NAME in out


def test_settings_set_persists_and_masks_a_secret_field_on_echo(tmp_path, monkeypatch, capsys):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(sys, "argv", ["cli.py", "settings", "set", "gemini_api_key", "sk-ABCDEFGHIJKL1234"])

    cli.main()  # must not raise SystemExit

    out = capsys.readouterr().out
    assert "sk-ABCDEFGHIJKL1234" not in out  # the raw key must never be echoed back
    assert "1234" in out  # only the masked tail
    assert app_settings.load_settings()["gemini_api_key"] == "sk-ABCDEFGHIJKL1234"


def test_settings_set_non_secret_field_echoes_the_real_value(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(sys, "argv", ["cli.py", "settings", "set", "llm_provider", "openai"])

    cli.main()

    assert "llm_provider = openai" in capsys.readouterr().out
    assert app_settings.load_settings()["llm_provider"] == "openai"


def test_settings_set_rejects_an_unknown_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(sys, "argv", ["cli.py", "settings", "set", "not_a_real_field", "x"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2  # argparse's own choices= validation, not a custom check


def test_settings_set_strips_surrounding_whitespace(tmp_path, monkeypatch, capsys):
    # Matches gui_server.py's POST /api/settings ((data.get(field) or
    # "").strip()) -- a pasted value with a trailing newline/space must not
    # reach llm.py's Authorization header verbatim (code-review finding).
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(sys, "argv", ["cli.py", "settings", "set", "gemini_api_key", "  sk-abc123 \n"])

    cli.main()

    assert app_settings.load_settings()["gemini_api_key"] == "sk-abc123"


def test_settings_set_empty_string_clears_a_field_back_to_unset(tmp_path, monkeypatch, capsys):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(sys, "argv", ["cli.py", "settings", "set", "gemini_model", "gemini-2.5-pro"])
    cli.main()
    assert app_settings.load_settings()["gemini_model"] == "gemini-2.5-pro"

    monkeypatch.setattr(sys, "argv", ["cli.py", "settings", "set", "gemini_model", ""])
    cli.main()

    assert app_settings.load_settings()["gemini_model"] == ""
    assert "미설정" in capsys.readouterr().out


def test_checkpoint_list_prints_project_name_and_pending_count(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_checkpoint, "CHECKPOINT_DIR", tmp_path)
    app_checkpoint.save_checkpoint(
        str(tmp_path / "proj"), {"project": {"name": "proj"}, "files_data": {"a.py": {}}}
    )
    monkeypatch.setattr(sys, "argv", ["cli.py", "checkpoint"])

    cli.main()  # must not raise SystemExit

    out = capsys.readouterr().out
    assert "proj" in out
    assert "1개" in out  # pending_files count


def test_checkpoint_list_reports_none_when_dir_is_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_checkpoint, "CHECKPOINT_DIR", tmp_path / "never-created")
    monkeypatch.setattr(sys, "argv", ["cli.py", "checkpoint", "list"])

    cli.main()

    assert "없음" in capsys.readouterr().out


def test_checkpoint_clean_with_path_removes_only_that_projects_checkpoint(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_checkpoint, "CHECKPOINT_DIR", tmp_path)
    proj1, proj2 = tmp_path / "proj1", tmp_path / "proj2"
    app_checkpoint.save_checkpoint(str(proj1), {"project": {"name": "proj1"}})
    app_checkpoint.save_checkpoint(str(proj2), {"project": {"name": "proj2"}})
    monkeypatch.setattr(sys, "argv", ["cli.py", "checkpoint", "clean", str(proj1)])

    cli.main()

    assert app_checkpoint.load_checkpoint(str(proj1)) is None
    assert app_checkpoint.load_checkpoint(str(proj2)) is not None  # untouched


def test_checkpoint_clean_all_removes_every_checkpoint(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_checkpoint, "CHECKPOINT_DIR", tmp_path)
    app_checkpoint.save_checkpoint(str(tmp_path / "proj1"), {"project": {"name": "proj1"}})
    app_checkpoint.save_checkpoint(str(tmp_path / "proj2"), {"project": {"name": "proj2"}})
    monkeypatch.setattr(sys, "argv", ["cli.py", "checkpoint", "clean", "--all"])

    cli.main()

    assert app_checkpoint.list_checkpoints() == []


def test_checkpoint_clean_without_path_or_all_errors_out(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_checkpoint, "CHECKPOINT_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["cli.py", "checkpoint", "clean"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2  # argparse-style usage error, not a crash


def test_doctor_prints_python_and_provider_status(monkeypatch, capsys):
    from ziplex import llm

    monkeypatch.setattr(llm, "describe_active_provider", lambda: {
        "name": "gemini", "model": "gemini-flash-latest", "api_key_present": True,
    })
    monkeypatch.setattr(cli.app_doctor, "_secretlint_available", lambda: False)
    monkeypatch.setattr(sys, "argv", ["cli.py", "doctor"])

    cli.main()  # must not raise SystemExit

    out = capsys.readouterr().out
    assert "gemini" in out
    assert "gemini-flash-latest" in out
    assert "API Key: 설정됨" in out
    assert "secretlint" in out  # unavailable-but-informational, not an error exit


def test_doctor_with_project_path_reports_missing_directory(tmp_path, monkeypatch, capsys):
    from ziplex import llm

    monkeypatch.setattr(llm, "describe_active_provider", lambda: {
        "name": "gemini", "model": "gemini-flash-latest", "api_key_present": False,
    })
    monkeypatch.setattr(cli.app_doctor, "_secretlint_available", lambda: True)
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(sys, "argv", ["cli.py", "doctor", str(missing)])

    cli.main()

    out = capsys.readouterr().out
    assert "❌" in out  # missing project dir and missing API key both render as ❌
    assert str(missing) in out


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


def test_freshness_does_not_report_a_previously_included_dangerous_file_as_removed(tmp_path, monkeypatch, capsys):
    # Real bug reported directly: a file flagged sensitive by scan_files()
    # but included in the pack anyway (a human opted back in via
    # review_dangerous_files()/the GUI's "include anyway" checkbox/a
    # preselected caller) gets re-flagged as dangerous on every later scan
    # regardless of that earlier decision -- `ziplex freshness` used to
    # only ever look at collect_and_scan()'s "safe" list, so it reported
    # this exact file as permanently removed even though it's unchanged
    # and still on disk.
    project = tmp_path / "project"
    project.mkdir()
    secret_path = project / "config.py"
    secret_path.write_text('API_KEY = "abc123"\n', encoding="utf-8")

    manifest = build_manifest([str(secret_path)], str(project))  # it WAS packed last time
    cache_path = tmp_path / "out.cache.json"
    cache_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["cli.py", "freshness", str(project), str(cache_path)])

    cli.main()  # must not raise SystemExit -- must not report it as stale/removed

    out = capsys.readouterr().out
    assert "최신 상태" in out
    assert "삭제됨" not in out


def test_tree_command_tags_a_text_reference_edge(tmp_path, monkeypatch, capsys):
    # Regression test for a real bug caught by code review: the `tree`
    # subcommand has its own independent copy of packager.py's "merge
    # text_references.py matches into dependencies" loop, and the first
    # version of that copy merged into `dependencies` only, never recording
    # `text_dependencies` -- so build_tree()'s internal_text_refs tagging
    # silently came back empty for this command specifically, even though
    # the exact same feature worked correctly through `pack`.
    project = tmp_path / "project"
    project.mkdir()
    (project / "config.py").write_text("x = 1\n", encoding="utf-8")
    # README.md has no Tree-sitter grammar, so extract_dependencies() alone
    # never connects it to config.py -- only text_references.py can.
    (project / "README.md").write_text("See config.py for settings.\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["cli.py", "tree", str(project)])
    cli.main()

    out = capsys.readouterr().out
    assert "config.py" in out
    assert "텍스트 언급" in out  # file/relationship.py's print_tree() marker


def test_tree_command_resolves_an_internal_go_package_import(tmp_path, monkeypatch, capsys):
    # Same "tree has its own copy of the merge loop" risk as the text-
    # reference regression above, this time for go_packages.py -- a bare
    # extract_dependencies() call would leave main.go's import unresolved
    # (a package path, not a file), so its target must never be an "external"
    # leaf here.
    project = tmp_path / "project"
    project.mkdir()
    (project / "go.mod").write_text("module example.com/myproject\n\ngo 1.21\n", encoding="utf-8")
    (project / "main.go").write_text(
        'package main\n\nimport "example.com/myproject/internal/utils"\n\nfunc main() {}\n', encoding="utf-8"
    )
    (project / "internal" / "utils").mkdir(parents=True)
    (project / "internal" / "utils" / "format.go").write_text(
        "package utils\n\nfunc Format() string { return \"\" }\n", encoding="utf-8"
    )

    monkeypatch.setattr(sys, "argv", ["cli.py", "tree", str(project)])
    cli.main()

    out = capsys.readouterr().out
    assert "internal/utils/format.go" in out
    assert "example.com/myproject/internal/utils" not in out  # resolved, not left as an external leaf


def test_analyze_command_resolves_an_internal_go_package_import(tmp_path, monkeypatch):
    # Regression test for a real bug caught by code review: `analyze` has
    # its own third copy of "extract_dependencies() on a .go file, then
    # feed it into a summary prompt" -- missed on this feature's first
    # pass, which only wired go_packages.py into pack()/tree.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    project.mkdir()
    (project / "go.mod").write_text("module example.com/myproject\n\ngo 1.21\n", encoding="utf-8")
    (project / "main.go").write_text(
        'package main\n\nimport "example.com/myproject/internal/utils"\n\nfunc main() {}\n', encoding="utf-8"
    )
    (project / "internal" / "utils").mkdir(parents=True)
    (project / "internal" / "utils" / "format.go").write_text(
        "package utils\n\nfunc Format() string { return \"\" }\n", encoding="utf-8"
    )

    captured = {}
    real_generate_summaries = cli.generate_summaries

    def _capturing(pending, root, lang="en"):
        captured.update(pending)
        return real_generate_summaries(pending, root, lang=lang)

    monkeypatch.setattr(cli, "generate_summaries", _capturing)
    monkeypatch.setattr(sys, "argv", ["cli.py", "analyze", str(project)])
    cli.main()

    main_go = next(data for file, data in captured.items() if file.endswith("main.go"))
    assert "internal/utils/format.go" in main_go["dependencies"]
    assert "example.com/myproject/internal/utils" not in main_go["dependencies"]


def test_analyze_command_delegates_to_summarizer_and_shares_its_failure_placeholder(tmp_path, monkeypatch, capsys):
    # analyze used to call llm.analyze_file_summary() directly in its own
    # bespoke per-file loop -- no batching, no shared retry-once-then-
    # placeholder logic, and its own separate failure string ("분석 실패")
    # instead of summarizer.SUMMARY_FAILED_PLACEHOLDERS's default-language
    # ("Summary generation failed"), the one confidence.py specifically
    # recognizes via is_summary_failed_placeholder(). Refactored to delegate
    # to summarizer.generate_summaries() -- the same path pack() itself
    # uses -- so a future fix there (batching, retry, placeholder handling)
    # no longer silently misses this command.
    class _FailingProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            return "not valid json"

    monkeypatch.setattr(llm, "_provider", _FailingProvider())

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["cli.py", "analyze", str(project)])
    cli.main()

    out = capsys.readouterr().out
    assert summarizer.SUMMARY_FAILED_PLACEHOLDERS["en"] in out
    assert "분석 실패" not in out
