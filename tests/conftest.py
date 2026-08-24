"""Shared pytest fixtures -- test-suite-wide isolation, not test logic
belonging to any one module.
"""

import pytest

from ziplex import settings as app_settings


@pytest.fixture(autouse=True)
def _isolate_ziplex_settings(tmp_path, monkeypatch):
    """Every test gets its own settings.json under its own tmp_path, never
    the real ~/.ziplex/settings.json. Without this, any test that exercises
    gui/pack_service.py's start_pack_job() with an explicit output_path
    (which calls settings.set_project_output_dir()) silently writes a real
    pin into the developer's actual home directory on every test run --
    caught after the fact via a real ~/.ziplex/settings.json that had
    accumulated dozens of pytest-tmp-path pins from exactly this. Individual
    tests can still monkeypatch SETTINGS_PATH again themselves (e.g. to
    share one exact path across multiple calls within the same test) --
    this just sets a safe default so a test that never mentions settings.py
    at all can't leak into real state by accident.
    """
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / ".ziplex-test-settings.json")
