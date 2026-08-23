import json

import settings


def test_load_settings_returns_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")

    assert settings.load_settings() == {"output_dir": "", "project_output_dirs": {}, "gemini_api_key": ""}


def test_load_settings_falls_back_to_defaults_on_invalid_json(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    assert settings.load_settings() == {"output_dir": "", "project_output_dirs": {}, "gemini_api_key": ""}


def test_load_settings_falls_back_to_defaults_when_not_an_object(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    assert settings.load_settings() == {"output_dir": "", "project_output_dirs": {}, "gemini_api_key": ""}


def test_load_settings_ignores_a_non_dict_project_output_dirs(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"output_dir": "D:/out", "project_output_dirs": "bogus"}), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    loaded = settings.load_settings()
    assert loaded["output_dir"] == "D:/out"
    assert loaded["project_output_dirs"] == {}


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "nested" / "settings.json")

    settings.save_settings({"output_dir": "D:/out", "project_output_dirs": {"C:/proj": "D:/proj-out"}, "gemini_api_key": "abc123"})

    assert settings.load_settings() == {"output_dir": "D:/out", "project_output_dirs": {"C:/proj": "D:/proj-out"}, "gemini_api_key": "abc123"}


def test_resolve_output_dir_prefers_project_pin_over_global_default(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    project = tmp_path / "my-project"
    project.mkdir()

    settings.save_settings({"output_dir": "D:/global", "project_output_dirs": {str(project.resolve()): "D:/pinned"}})

    assert settings.resolve_output_dir(str(project)) == "D:/pinned"


def test_resolve_output_dir_falls_back_to_global_default_when_unpinned(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    project = tmp_path / "my-project"
    project.mkdir()

    settings.save_settings({"output_dir": "D:/global", "project_output_dirs": {}})

    assert settings.resolve_output_dir(str(project)) == "D:/global"


def test_resolve_output_dir_returns_empty_string_when_nothing_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    project = tmp_path / "my-project"
    project.mkdir()

    assert settings.resolve_output_dir(str(project)) == ""


def test_resolve_output_path_joins_resolved_dir_with_project_name(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    project = tmp_path / "my-project"
    project.mkdir()
    settings.save_settings({"output_dir": str(tmp_path / "out"), "project_output_dirs": {}})

    assert settings.resolve_output_path(str(project), "my-project") == str(tmp_path / "out" / "my-project.json")


def test_resolve_output_path_returns_empty_string_when_nothing_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    project = tmp_path / "my-project"
    project.mkdir()

    assert settings.resolve_output_path(str(project), "my-project") == ""


def test_set_project_output_dir_persists_a_pin_without_touching_the_global_default(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    project = tmp_path / "my-project"
    project.mkdir()
    settings.save_settings({"output_dir": "D:/global", "project_output_dirs": {}})

    settings.set_project_output_dir(str(project), "D:/pinned")

    loaded = settings.load_settings()
    assert loaded["output_dir"] == "D:/global"
    assert loaded["project_output_dirs"][str(project.resolve())] == "D:/pinned"


def test_set_project_output_dir_keyed_by_resolved_absolute_path(tmp_path, monkeypatch):
    # a relative or differently-formed path to the same project should hit
    # the same pin -- resolve_output_dir() and set_project_output_dir() both
    # normalize through Path.resolve() for exactly this reason
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    project = tmp_path / "my-project"
    project.mkdir()

    settings.set_project_output_dir(str(project), "D:/pinned")

    assert settings.resolve_output_dir(str(project) + "/") == "D:/pinned"


def test_resolve_gemini_api_key_returns_empty_string_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")

    assert settings.resolve_gemini_api_key() == ""


def test_resolve_gemini_api_key_returns_the_stored_key(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save_settings({"output_dir": "", "project_output_dirs": {}, "gemini_api_key": "my-key"})

    assert settings.resolve_gemini_api_key() == "my-key"
