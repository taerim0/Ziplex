"""gui_server.py's read-only /api/* routes: query params adapted to
query_service calls, JSON error shapes for bad paths/regexes/unknown files,
static file serving, the settings route, the watcher routes, and
_find_free_port()/_Api. The query logic itself is covered via
query_service's own tests.
"""

import json
import socket

import pytest

import ziplex
from ziplex import freshness
from ziplex.gui import gui_server
from ziplex import settings as app_settings


@pytest.fixture
def client():
    gui_server.app.testing = True
    return gui_server.app.test_client()


def _write_sample_aif(tmp_path):
    aif_path = tmp_path / "sample.json"
    aif_path.write_text(json.dumps({
        "project": {"name": "sample", "prompt": "A sample project."},
        "rules": ["rule one"],
        "tokens": {"GPT-4o": {"original": 100, "compressed": 20, "saved_pct": 80.0}},
        "files": {
            "a.py": {"summary": "does a thing"},
            "b.py": {"summary": "uses a.py"},
        },
        "relationships": {
            "a.py": {"internal": [], "external": []},
            "b.py": {"internal": ["a.py"], "external": []},
        },
    }), encoding="utf-8")
    (tmp_path / "sample.detail.json").write_text(json.dumps({
        "a.py": {"compressed": "def thing():\n    ⋮----\n"},
        "b.py": {"compressed": "import a\n"},
    }), encoding="utf-8")
    return str(aif_path)


def test_index_serves_static_shell(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b'type="module" src="js/router.js"' in res.data


def test_static_assets_are_served(client):
    # index.html loads only js/router.js; every module it imports is
    # fetched as its own request.
    for name in [
        "js/i18n.js", "js/app.js", "js/graph.js", "js/router.js", "js/theme.js",
        "js/pages/landing.js", "js/pages/options.js", "js/pages/overview.js",
        "js/pages/files.js", "js/pages/relationships.js", "js/pages/search.js",
    ]:
        assert client.get(f"/{name}").status_code == 200, name
    assert client.get("/style.css").status_code == 200


def test_the_pack_flow_frontend_is_gone(client):
    assert client.get("/js/pack.js").status_code == 404


def test_only_settings_and_watch_start_accept_writes():
    # Read-only viewer: nothing may pack, edit, or rewrite aif.json.
    writable = sorted(
        rule.rule for rule in gui_server.app.url_map.iter_rules()
        if rule.methods & {"POST", "PUT", "PATCH", "DELETE"}
    )
    assert writable == ["/api/settings", "/api/watch/start"]


@pytest.mark.parametrize("path", [
    "/api/pack", "/api/pack/finalize", "/api/relationships/link", "/api/files/summary",
])
def test_removed_write_routes_are_gone(client, path):
    # 405, not 404: the catch-all static route still matches GET.
    assert client.post(path, json={}).status_code == 405


def test_config_reflects_startup_defaults(client):
    gui_server._default_config["aif_path"] = "some/path.json"
    gui_server._default_config["project_path"] = None
    res = client.get("/api/config")
    # "version" is a static, always-present field (see gui_server.py's
    # _default_config comment) -- checked separately rather than pinned to
    # a literal so this test doesn't need a version bump every release.
    data = res.get_json()
    assert data.pop("version") == ziplex.__version__
    assert data == {"aif_path": "some/path.json", "project_path": None}
    gui_server._default_config["aif_path"] = None  # reset for other tests


def test_settings_get_returns_defaults_when_unconfigured(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    res = client.get("/api/settings")
    assert res.get_json() == app_settings.DEFAULT_SETTINGS


def test_settings_post_sets_the_global_output_dir(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    res = client.post("/api/settings", json={"output_dir": str(tmp_path / "out")})
    assert res.get_json()["output_dir"] == str(tmp_path / "out")
    assert app_settings.load_settings()["output_dir"] == str(tmp_path / "out")


def test_settings_post_does_not_clobber_project_pins(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    app_settings.save_settings({"output_dir": "", "project_output_dirs": {"C:/proj": "D:/pinned"}, "gemini_api_key": ""})

    client.post("/api/settings", json={"output_dir": str(tmp_path / "out")})

    loaded = app_settings.load_settings()
    assert loaded["output_dir"] == str(tmp_path / "out")
    assert loaded["project_output_dirs"] == {"C:/proj": "D:/pinned"}


def test_settings_post_sets_the_gemini_api_key(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    res = client.post("/api/settings", json={"gemini_api_key": "my-secret-key"})
    assert res.get_json()["gemini_api_key"] == "my-secret-key"
    assert app_settings.load_settings()["gemini_api_key"] == "my-secret-key"


def test_settings_post_api_key_does_not_clobber_output_dir(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    app_settings.save_settings({"output_dir": "D:/out", "project_output_dirs": {}, "gemini_api_key": ""})

    client.post("/api/settings", json={"gemini_api_key": "my-secret-key"})

    loaded = app_settings.load_settings()
    assert loaded["output_dir"] == "D:/out"
    assert loaded["gemini_api_key"] == "my-secret-key"


def test_settings_post_sets_the_gemini_model(client, tmp_path, monkeypatch):
    # The Options-page field added specifically so a GUI-only user (no .env
    # access) can move off GeminiProvider.DEFAULT_MODEL -- see that
    # constant's own comment for the real external-tester report this
    # fixed.
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    res = client.post("/api/settings", json={"gemini_model": "gemini-3.5-flash"})
    assert res.get_json()["gemini_model"] == "gemini-3.5-flash"
    assert app_settings.load_settings()["gemini_model"] == "gemini-3.5-flash"


def test_settings_post_sets_the_llm_provider_and_openai_fields_together(client, tmp_path, monkeypatch):
    # The provider selector saves its choice alongside that provider's own
    # fields in one request -- see js/pages/options.js -- so switching to
    # OpenAI and saving a key can never land in a state where the key is
    # stored but llm_provider still says "gemini" (or vice versa).
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    res = client.post("/api/settings", json={
        "llm_provider": "openai",
        "openai_api_key": "sk-abc",
        "openai_base_url": "http://localhost:11434/v1",
        "openai_model": "gemma2",
    })
    body = res.get_json()
    assert body["llm_provider"] == "openai"
    assert body["openai_api_key"] == "sk-abc"
    assert body["openai_base_url"] == "http://localhost:11434/v1"
    assert body["openai_model"] == "gemma2"


def test_settings_post_sets_the_claude_fields(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    res = client.post("/api/settings", json={
        "llm_provider": "claude",
        "claude_api_key": "claude-key",
        "claude_model": "claude-sonnet-4-5",
    })
    body = res.get_json()
    assert body["llm_provider"] == "claude"
    assert body["claude_api_key"] == "claude-key"
    assert body["claude_model"] == "claude-sonnet-4-5"


def test_api_overview(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/overview", query_string={"aif_path": aif_path})
    assert res.status_code == 200
    data = res.get_json()
    assert data["project"]["name"] == "sample"
    assert data["file_count"] == 2
    assert "_stale" not in data


def test_api_overview_requires_aif_path(client):
    res = client.get("/api/overview")
    assert res.status_code == 400


def test_api_files(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/files", query_string={"aif_path": aif_path})
    data = res.get_json()
    assert {name: info["summary"] for name, info in data.items()} == {
        "a.py": "does a thing", "b.py": "uses a.py",
    }


def test_api_files_scopes_by_folder(client, tmp_path):
    aif_path = tmp_path / "sample.json"
    aif_path.write_text(json.dumps({
        "project": {"name": "sample"},
        "files": {
            "a.py": {"summary": "root file"},
            "src/b.py": {"summary": "nested file"},
        },
    }), encoding="utf-8")

    res = client.get("/api/files", query_string={"aif_path": str(aif_path), "folder": "src"})
    data = res.get_json()
    assert list(data) == ["src/b.py"]
    assert data["src/b.py"]["summary"] == "nested file"


def test_api_folders(client, tmp_path):
    aif_path = tmp_path / "sample.json"
    aif_path.write_text(json.dumps({
        "project": {"name": "sample"},
        "files": {"a.py": {"summary": "does a thing"}},
        "folders": {".": {"summary": "Top-level project files."}},
    }), encoding="utf-8")

    res = client.get("/api/folders", query_string={"aif_path": str(aif_path)})
    assert res.get_json() == {".": {"summary": "Top-level project files."}}


def test_api_folders_missing_field_returns_empty_dict(client, tmp_path):
    # A project packed before "folders" existed -- the route must degrade
    # to {} rather than a 500, same backward-compat contract query_service.
    # get_folders() itself already guarantees.
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/folders", query_string={"aif_path": aif_path})
    assert res.get_json() == {}


def test_api_files_attaches_stale_warning(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "sample.cache.json").write_text(
        json.dumps(freshness.build_manifest([str(project / "a.py")], str(project))),
        encoding="utf-8",
    )
    (project / "a.py").write_text("x = 2\n", encoding="utf-8")  # edit after cache taken

    res = client.get("/api/files", query_string={"aif_path": aif_path, "project_path": str(project)})
    data = res.get_json()
    assert data["_stale"]["is_stale"] is True


def test_api_dependents(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/dependents", query_string={"aif_path": aif_path, "file": "a.py"})
    assert res.get_json() == ["b.py"]


def test_api_relationships(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/relationships", query_string={"aif_path": aif_path})
    assert res.get_json() == {
        "a.py": {"internal": [], "external": [], "internal_text_refs": []},
        "b.py": {"internal": ["a.py"], "external": [], "internal_text_refs": []},
    }


def test_api_relationships_scopes_by_files(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/relationships", query_string={"aif_path": aif_path, "files": ["b.py"]})
    assert res.get_json() == {"b.py": {"internal": ["a.py"], "external": [], "internal_text_refs": []}}


def test_api_blast_radius(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/blast_radius", query_string={"aif_path": aif_path, "file": "a.py"})
    assert res.get_json() == ["b.py"]


def _write_text_ref_aif(tmp_path):
    # b.py's only link to a.py is a text_references.py filename mention, not
    # a real import -- the shape include_text_refs=False is supposed to
    # exclude.
    aif_path = tmp_path / "textref.json"
    aif_path.write_text(json.dumps({
        "project": {"name": "sample", "prompt": "A sample project."},
        "files": {
            "a.py": {"summary": "does a thing"},
            "b.py": {"summary": "mentions a.py in a docstring"},
        },
        "relationships": {
            "a.py": {"internal": [], "external": [], "internal_text_refs": []},
            "b.py": {"internal": ["a.py"], "external": [], "internal_text_refs": ["a.py"]},
        },
    }), encoding="utf-8")
    return str(aif_path)


def test_api_dependents_include_text_refs_false_excludes_text_only_match(client, tmp_path):
    # A code-review finding: /api/dependents used to hard-code the default
    # and never read this param from the request at all, so the GUI had no
    # way to reach query_service.py's "certain relationships only" view --
    # already real end-to-end and already exposed by the MCP server.
    aif_path = _write_text_ref_aif(tmp_path)
    res = client.get("/api/dependents", query_string={"aif_path": aif_path, "file": "a.py"})
    assert res.get_json() == ["b.py"]

    res = client.get(
        "/api/dependents",
        query_string={"aif_path": aif_path, "file": "a.py", "include_text_refs": "false"},
    )
    assert res.get_json() == []


def test_api_blast_radius_include_text_refs_false_excludes_text_only_match(client, tmp_path):
    aif_path = _write_text_ref_aif(tmp_path)
    res = client.get("/api/blast_radius", query_string={"aif_path": aif_path, "file": "a.py"})
    assert res.get_json() == ["b.py"]

    res = client.get(
        "/api/blast_radius",
        query_string={"aif_path": aif_path, "file": "a.py", "include_text_refs": "false"},
    )
    assert res.get_json() == []


def test_api_detail(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/detail", query_string={"aif_path": aif_path, "file": "a.py"})
    assert res.status_code == 200
    assert res.get_json() == {"compressed": "def thing():\n    ⋮----"}


def test_api_detail_missing_file_is_404_not_500(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/detail", query_string={"aif_path": aif_path, "file": "missing.py"})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_api_detail_line_range(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/detail", query_string={"aif_path": aif_path, "file": "a.py", "start_line": 1, "end_line": 1})
    assert res.get_json() == {"compressed": "def thing():"}


def test_api_freshness(client, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")

    aif_path = tmp_path / "sample.json"
    aif_path.write_text(json.dumps({"project": {"name": "sample"}}), encoding="utf-8")
    (tmp_path / "sample.cache.json").write_text(
        json.dumps(freshness.build_manifest([str(project / "a.py")], str(project))),
        encoding="utf-8",
    )

    res = client.get("/api/freshness", query_string={"project_path": str(project), "aif_path": str(aif_path)})
    assert res.get_json() == {
        "is_stale": False, "changed": [], "added": [], "removed": [], "unchanged_count": 1,
    }


def _make_watch_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    aif_path = tmp_path / "sample.json"
    aif_path.write_text(json.dumps({"project": {"name": "sample"}}), encoding="utf-8")
    (tmp_path / "sample.cache.json").write_text(
        json.dumps(freshness.build_manifest([str(project / "a.py")], str(project))),
        encoding="utf-8",
    )
    return project, aif_path


def test_api_watch_start_then_status_reports_fresh(client, tmp_path, monkeypatch):
    from ziplex.gui import watcher
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.05)
    project, aif_path = _make_watch_project(tmp_path)

    start = client.post("/api/watch/start", json={"project_path": str(project), "aif_path": str(aif_path)})
    assert start.status_code == 200

    status = client.get("/api/watch/status", query_string={"project_path": str(project)})
    assert status.get_json()["report"]["is_stale"] is False

    watcher.stop_watch(str(project))  # don't leave a background Observer running past this test


def test_api_watch_status_returns_none_report_when_never_started(client, tmp_path):
    res = client.get("/api/watch/status", query_string={"project_path": str(tmp_path)})
    assert res.get_json() == {"report": None}


def test_api_watch_start_requires_project_path_and_aif_path(client, tmp_path):
    res = client.post("/api/watch/start", json={"project_path": str(tmp_path)})
    assert res.status_code == 400


def test_api_watch_start_404s_on_a_missing_project_path(client, tmp_path):
    res = client.post("/api/watch/start", json={
        "project_path": str(tmp_path / "does-not-exist"), "aif_path": str(tmp_path / "x.json"),
    })
    assert res.status_code == 404


def test_api_watch_start_404s_on_a_missing_cache_json(client, tmp_path):
    # Regression, found by code review: watcher.start_watch() itself never
    # raises on a missing/typo'd aif_path (a missing cache.json is
    # swallowed inside its own recompute()), so without this check the
    # route would have silently reported {"ok": true} for an aif_path with
    # no sibling cache.json, leaving the watch permanently stuck on a null
    # report with no error ever surfaced to the caller.
    project = tmp_path / "project"
    project.mkdir()
    res = client.post("/api/watch/start", json={
        "project_path": str(project), "aif_path": str(tmp_path / "never-packed.json"),
    })
    assert res.status_code == 404


def test_api_search(client, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")

    res = client.get("/api/search", query_string={"project_path": str(project), "pattern": "hello"})
    assert res.status_code == 200
    data = res.get_json()
    assert len(data) == 1
    assert data[0]["file"] == "a.py"
    assert data[0]["line"] == 1


def test_api_search_invalid_regex_is_400_not_500(client, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    res = client.get("/api/search", query_string={"project_path": str(project), "pattern": "("})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_api_overview_missing_aif_path_is_404_not_500(client, tmp_path):
    res = client.get("/api/overview", query_string={"aif_path": str(tmp_path / "does_not_exist.json")})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_api_dependents_missing_aif_path_is_404_not_500(client, tmp_path):
    res = client.get("/api/dependents", query_string={"aif_path": str(tmp_path / "nope.json"), "file": "a.py"})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_api_overview_corrupt_json_is_400_not_500(client, tmp_path):
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not valid json", encoding="utf-8")
    res = client.get("/api/overview", query_string={"aif_path": str(bad)})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_find_free_port_returns_preferred_when_free():
    # an ephemeral port from the OS is (almost certainly) free
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    assert gui_server._find_free_port(free_port) == free_port


def test_find_free_port_skips_a_port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        occupied_port = s.getsockname()[1]
        found = gui_server._find_free_port(occupied_port)
        assert found != occupied_port
        assert occupied_port < found <= occupied_port + 49


class _FakeFileDialog:
    FOLDER = "folder"
    OPEN = "open"


class _FakeWindow:
    def __init__(self, dialog_result=None):
        self._dialog_result = dialog_result
        self.create_file_dialog_calls = []

    def create_file_dialog(self, *args, **kwargs):
        self.create_file_dialog_calls.append((args, kwargs))
        return self._dialog_result


class _FakeWebview:
    FileDialog = _FakeFileDialog

    def __init__(self, window):
        self.windows = [window]


# _Api takes the webview module as an argument, so a fake stands in for the
# real one (which needs an OS window).

def test_api_choose_folder_returns_the_picked_path():
    window = _FakeWindow(dialog_result=["C:/some/project"])
    api = gui_server._Api(_FakeWebview(window))
    assert api.choose_folder() == "C:/some/project"
    assert window.create_file_dialog_calls[0][0] == ("folder",)


def test_api_choose_folder_returns_none_when_dialog_cancelled():
    window = _FakeWindow(dialog_result=None)
    api = gui_server._Api(_FakeWebview(window))
    assert api.choose_folder() is None


def test_api_choose_aif_file_uses_an_open_dialog_filtered_to_json():
    window = _FakeWindow(dialog_result=["C:/some/out.json"])
    api = gui_server._Api(_FakeWebview(window))
    assert api.choose_aif_file() == "C:/some/out.json"
    args, kwargs = window.create_file_dialog_calls[0]
    assert args == ("open",)
    assert kwargs["file_types"] == gui_server._JSON_FILE_TYPES
