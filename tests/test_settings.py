import json

from ziplex import settings


def test_load_settings_returns_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")

    assert settings.load_settings() == settings.DEFAULT_SETTINGS


def test_load_settings_falls_back_to_defaults_on_invalid_json(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    assert settings.load_settings() == settings.DEFAULT_SETTINGS


def test_load_settings_falls_back_to_defaults_when_not_an_object(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    assert settings.load_settings() == settings.DEFAULT_SETTINGS


def test_load_settings_defaults_do_not_alias_the_module_level_dict(tmp_path, monkeypatch):
    # Regression guard for a real bug introduced and caught in the same
    # session that added the LLM-provider fields: an earlier version of the
    # no-file/bad-file fallback returned dict(DEFAULT_SETTINGS) -- a shallow
    # copy, so its "project_output_dirs" value was still the exact same {}
    # object as DEFAULT_SETTINGS' own. set_project_output_dir() mutates
    # that key in place, so calling it once (with no settings.json yet)
    # would silently corrupt the module-level default for the rest of the
    # process -- every later load_settings() call landing back here would
    # then wrongly appear to already have that project's pin.
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    project = tmp_path / "my-project"
    project.mkdir()

    settings.set_project_output_dir(str(project), "D:/pinned")

    assert settings.DEFAULT_SETTINGS["project_output_dirs"] == {}


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

    assert settings.load_settings() == {**settings.DEFAULT_SETTINGS, "output_dir": "D:/out", "project_output_dirs": {"C:/proj": "D:/proj-out"}, "gemini_api_key": "abc123"}


def test_save_then_load_round_trips_the_llm_provider_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")

    settings.save_settings({
        **settings.DEFAULT_SETTINGS,
        "llm_provider": "openai",
        "gemini_model": "gemini-3.5-flash",
        "openai_api_key": "sk-abc",
        "openai_base_url": "http://localhost:11434/v1",
        "openai_model": "gemma2",
        "claude_api_key": "claude-key",
        "claude_model": "claude-sonnet-4-5",
    })

    loaded = settings.load_settings()
    assert loaded["llm_provider"] == "openai"
    assert loaded["gemini_model"] == "gemini-3.5-flash"
    assert loaded["openai_api_key"] == "sk-abc"
    assert loaded["openai_base_url"] == "http://localhost:11434/v1"
    assert loaded["openai_model"] == "gemma2"
    assert loaded["claude_api_key"] == "claude-key"
    assert loaded["claude_model"] == "claude-sonnet-4-5"


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


def test_resolve_gemini_model_returns_empty_string_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")

    assert settings.resolve_gemini_model() == ""


def test_resolve_gemini_model_returns_the_stored_model(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save_settings({**settings.DEFAULT_SETTINGS, "gemini_model": "gemini-3.5-flash"})

    assert settings.resolve_gemini_model() == "gemini-3.5-flash"


def test_resolve_llm_provider_name_returns_empty_string_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")

    assert settings.resolve_llm_provider_name() == ""


def test_resolve_llm_provider_name_returns_the_stored_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save_settings({**settings.DEFAULT_SETTINGS, "llm_provider": "claude"})

    assert settings.resolve_llm_provider_name() == "claude"


def test_resolve_openai_fields_return_the_stored_values(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save_settings({
        **settings.DEFAULT_SETTINGS,
        "openai_api_key": "sk-abc",
        "openai_base_url": "http://localhost:11434/v1",
        "openai_model": "gemma2",
    })

    assert settings.resolve_openai_api_key() == "sk-abc"
    assert settings.resolve_openai_base_url() == "http://localhost:11434/v1"
    assert settings.resolve_openai_model() == "gemma2"


def test_resolve_claude_fields_return_the_stored_values(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save_settings({**settings.DEFAULT_SETTINGS, "claude_api_key": "claude-key", "claude_model": "claude-sonnet-4-5"})

    assert settings.resolve_claude_api_key() == "claude-key"
    assert settings.resolve_claude_model() == "claude-sonnet-4-5"
