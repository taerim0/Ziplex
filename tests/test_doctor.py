"""Covers doctor.py's run_diagnostics()/_secretlint_available() -- the
network-free environment check behind `ziplex doctor`. Everything here is
monkeypatched (sys.version_info, llm.describe_active_provider(),
settings.SETTINGS_PATH, checkpoint.CHECKPOINT_DIR, subprocess.run) so this
suite never depends on this machine's real Python version, real
~/.ziplex/settings.json, or secretlint actually being installed.
"""

import subprocess
from types import SimpleNamespace

from ziplex import checkpoint
from ziplex import doctor
from ziplex import llm
from ziplex import settings as app_settings


def _fake_provider(name="gemini", model="gemini-flash-latest", api_key_present=True):
    return {"name": name, "model": model, "api_key_present": api_key_present}


def test_run_diagnostics_reports_python_ok_for_a_supported_version(monkeypatch):
    monkeypatch.setattr(doctor.sys, "version_info", SimpleNamespace(major=3, minor=11, micro=5))
    monkeypatch.setattr(llm, "describe_active_provider", lambda: _fake_provider())
    monkeypatch.setattr(doctor, "_secretlint_available", lambda: True)

    report = doctor.run_diagnostics()

    assert report["python_ok"] is True
    assert report["python_version"] == "3.11.5"


def test_run_diagnostics_reports_python_not_ok_below_the_minimum(monkeypatch):
    monkeypatch.setattr(doctor.sys, "version_info", SimpleNamespace(major=3, minor=9, micro=0))
    monkeypatch.setattr(llm, "describe_active_provider", lambda: _fake_provider())
    monkeypatch.setattr(doctor, "_secretlint_available", lambda: True)

    report = doctor.run_diagnostics()

    assert report["python_ok"] is False
    assert report["python_min"] == "3.10"


def test_run_diagnostics_reflects_the_active_provider_description(monkeypatch):
    monkeypatch.setattr(
        llm, "describe_active_provider", lambda: _fake_provider("claude", "claude-sonnet-4-5", False)
    )
    monkeypatch.setattr(doctor, "_secretlint_available", lambda: True)

    report = doctor.run_diagnostics()

    assert report["llm_provider"] == "claude"
    assert report["llm_model"] == "claude-sonnet-4-5"
    assert report["llm_api_key_present"] is False


def test_run_diagnostics_settings_file_present_reflects_the_real_path(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "describe_active_provider", lambda: _fake_provider())
    monkeypatch.setattr(doctor, "_secretlint_available", lambda: True)

    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "missing.json")
    assert doctor.run_diagnostics()["settings_file_present"] is False

    present = tmp_path / "settings.json"
    present.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", present)
    assert doctor.run_diagnostics()["settings_file_present"] is True


def test_run_diagnostics_checkpoint_count_reflects_leftover_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "describe_active_provider", lambda: _fake_provider())
    monkeypatch.setattr(doctor, "_secretlint_available", lambda: True)
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
    checkpoint.save_checkpoint(str(tmp_path / "proj"), {"project": {"name": "proj"}})

    assert doctor.run_diagnostics()["checkpoint_count"] == 1


def test_run_diagnostics_without_project_path_omits_project_keys(monkeypatch):
    monkeypatch.setattr(llm, "describe_active_provider", lambda: _fake_provider())
    monkeypatch.setattr(doctor, "_secretlint_available", lambda: True)

    report = doctor.run_diagnostics()

    assert "project_path" not in report
    assert "project_is_dir" not in report


def test_run_diagnostics_with_project_path_reports_env_and_git_presence(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "describe_active_provider", lambda: _fake_provider())
    monkeypatch.setattr(doctor, "_secretlint_available", lambda: True)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=x", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    report = doctor.run_diagnostics(str(tmp_path))

    assert report["project_is_dir"] is True
    assert report["project_has_env_file"] is True
    assert report["project_is_git_repo"] is True


def test_run_diagnostics_with_a_missing_project_path_reports_it_as_such(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "describe_active_provider", lambda: _fake_provider())
    monkeypatch.setattr(doctor, "_secretlint_available", lambda: True)

    report = doctor.run_diagnostics(str(tmp_path / "does-not-exist"))

    assert report["project_is_dir"] is False


def test_secretlint_available_returns_false_when_the_binary_is_missing(monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", _raise)
    assert doctor._secretlint_available() is False


def test_secretlint_available_returns_false_on_timeout(monkeypatch):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="secretlint", timeout=5)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert doctor._secretlint_available() is False


def test_secretlint_available_returns_true_when_it_runs(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert doctor._secretlint_available() is True


def test_min_python_reads_the_real_pyprojects_requires_python():
    # Regression guard for the code-review finding this replaced a
    # hardcoded (3, 10) constant with: must actually reflect pyproject.toml's
    # own `requires-python`, not a second copy that could drift from it.
    assert doctor._min_python() == (3, 10)
    assert doctor.MIN_PYTHON == (3, 10)


def test_min_python_falls_back_when_pyproject_toml_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "_PYPROJECT_PATH", tmp_path / "does-not-exist.toml")
    assert doctor._min_python() == doctor._FALLBACK_MIN_PYTHON


def test_min_python_falls_back_when_tomllib_is_unavailable(monkeypatch):
    monkeypatch.setattr(doctor, "tomllib", None)
    assert doctor._min_python() == doctor._FALLBACK_MIN_PYTHON


def test_min_python_falls_back_on_a_malformed_pyproject_toml(monkeypatch, tmp_path):
    bad = tmp_path / "pyproject.toml"
    bad.write_text("not valid toml [[[", encoding="utf-8")
    monkeypatch.setattr(doctor, "_PYPROJECT_PATH", bad)
    assert doctor._min_python() == doctor._FALLBACK_MIN_PYTHON


def test_min_python_falls_back_when_requires_python_field_is_absent(monkeypatch, tmp_path):
    missing_field = tmp_path / "pyproject.toml"
    missing_field.write_text('[project]\nname = "x"\n', encoding="utf-8")
    monkeypatch.setattr(doctor, "_PYPROJECT_PATH", missing_field)
    assert doctor._min_python() == doctor._FALLBACK_MIN_PYTHON


def test_min_python_parses_a_multi_clause_requires_python(monkeypatch, tmp_path):
    multi_clause = tmp_path / "pyproject.toml"
    multi_clause.write_text('[project]\nrequires-python = ">=3.10,<4.0"\n', encoding="utf-8")
    monkeypatch.setattr(doctor, "_PYPROJECT_PATH", multi_clause)

    assert doctor._min_python() == (3, 10)
