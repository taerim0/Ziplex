import json

from ziplex.config import load_config, init_config, DEFAULT_CONFIG, CONFIG_FILENAME


def test_load_config_returns_defaults_when_no_file(tmp_path):
    assert load_config(str(tmp_path)) == DEFAULT_CONFIG


def test_load_config_merges_partial_file_over_defaults(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"include": ["src/**"]}), encoding="utf-8")

    config = load_config(str(tmp_path))
    assert config["include"] == ["src/**"]
    assert config["ignore"] == []  # not present in the file -- default kept


def test_load_config_ignores_unknown_keys(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"include": ["a"], "bogus": 1}), encoding="utf-8")

    config = load_config(str(tmp_path))
    assert "bogus" not in config


def test_load_config_falls_back_to_defaults_on_invalid_json(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text("{not valid json", encoding="utf-8")

    assert load_config(str(tmp_path)) == DEFAULT_CONFIG


def test_load_config_falls_back_to_defaults_when_not_an_object(tmp_path):
    # a syntactically valid JSON array, but not the object shape expected
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps(["src/**"]), encoding="utf-8")

    assert load_config(str(tmp_path)) == DEFAULT_CONFIG


def test_init_config_writes_defaults(tmp_path):
    target = init_config(str(tmp_path))

    assert target == str(tmp_path / CONFIG_FILENAME)
    assert json.loads((tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8")) == DEFAULT_CONFIG


def test_init_config_does_not_overwrite_an_existing_file(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"include": ["keep-me/**"]}), encoding="utf-8")

    init_config(str(tmp_path))

    assert load_config(str(tmp_path))["include"] == ["keep-me/**"]
