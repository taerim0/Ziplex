"""Verifies gui_server.py's /api/* routes correctly adapt query params to
query_service.py calls and shape JSON responses -- not the underlying logic,
which is already covered via query_service through test_mcp_server.py (same
core, different transport) and test_relationship.py/test_search.py directly.
Also checks static file serving (index.html, the app-*.js frontend split,
style.css) and the real behavior that lives in gui_server.py itself: turning
get_detail's/search_project's ValueError, and a bad aif_path/project_path's
OSError/JSONDecodeError, into clean JSON error responses instead of a 500;
and _find_free_port()'s fallback when the requested port is already taken.
"""

import json
import socket
import threading
import time
from unittest import mock

import pytest

from ziplex import checkpoint
from ziplex import freshness
from ziplex.gui import gui_server
from ziplex import llm
from ziplex import packager
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
    # app.js used to be one flat file, then split into six flat app-*.js
    # files, then (see src/gui/CLAUDE.md's Frontend section) into real ES
    # modules under js/ -- index.html now loads only js/router.js, but
    # every module it transitively imports still has to be independently
    # servable, since the browser fetches each one as its own request.
    for name in [
        "js/i18n.js", "js/app.js", "js/graph.js", "js/pack.js", "js/router.js",
        "js/pages/landing.js", "js/pages/options.js", "js/pages/overview.js",
        "js/pages/files.js", "js/pages/relationships.js", "js/pages/search.js",
    ]:
        assert client.get(f"/{name}").status_code == 200, name
    assert client.get("/style.css").status_code == 200


def test_config_reflects_startup_defaults(client):
    gui_server._default_config["aif_path"] = "some/path.json"
    gui_server._default_config["project_path"] = None
    res = client.get("/api/config")
    assert res.get_json() == {"aif_path": "some/path.json", "project_path": None}
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


def _wait_for_job(client, job_id, timeout=10):
    since = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get("/api/pack/status", query_string={"job_id": job_id, "since": since})
        data = res.get_json()
        since = data["log_len"]
        if data["state"] != "running":
            return data
        time.sleep(0.02)
    raise AssertionError("pack job did not finish in time")


def test_api_select_files_splits_safe_and_dangerous(client, tmp_path):
    project = tmp_path / "project"
    (project).mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (project / "secret.env").write_text('API_KEY = "abc123"\n', encoding="utf-8")

    res = client.get("/api/select_files", query_string={"project_path": str(project)})
    assert res.status_code == 200
    data = res.get_json()
    assert "main.py" in data["safe"]
    # "dangerous" carries why, not just which -- a flat name list would
    # give a GUI file-selection screen nothing to show a human beyond "this
    # one's excluded, trust us"
    assert data["dangerous"] == [{
        "file": "secret.env", "reason": mock.ANY, "line": 1, "matched_text": 'API_KEY = "abc123"',
    }]


def test_api_select_files_missing_project_dir_is_404(client, tmp_path):
    res = client.get("/api/select_files", query_string={"project_path": str(tmp_path / "does_not_exist")})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_api_pack_requires_project_path(client):
    res = client.post("/api/pack", json={})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_api_pack_missing_project_dir_is_404(client, tmp_path):
    res = client.post("/api/pack", json={"project_path": str(tmp_path / "does_not_exist"), "selected_files": ["a.py"]})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_api_pack_requires_selected_files(client, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    res = client.post("/api/pack", json={"project_path": str(project), "selected_files": []})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_api_pack_status_unknown_job_is_404(client):
    res = client.get("/api/pack/status", query_string={"job_id": "no-such-job"})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_api_pack_review_unknown_job_is_404(client):
    res = client.get("/api/pack/review", query_string={"job_id": "no-such-job"})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_api_pack_finalize_requires_job_id(client):
    res = client.post("/api/pack/finalize", json={})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_api_pack_finalize_unknown_job_is_404(client):
    res = client.post("/api/pack/finalize", json={"job_id": "no-such-job"})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_api_pack_cancel_unknown_job_is_404(client):
    res = client.post("/api/pack/cancel", json={"job_id": "no-such-job"})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_api_pack_runs_end_to_end_with_mock_provider(client, tmp_path, monkeypatch):
    # This is the route-adapter test (starts the job over HTTP, polls it over
    # HTTP, reviews and finalizes it over HTTP, checks the job actually
    # reaches the filesystem) -- pack_service.py's own job-lifecycle behavior
    # (log capture, since= slicing, error state, review/edit application) is
    # covered directly in test_pack_service.py.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    output_path = tmp_path / "out" / "project.json"

    files = client.get("/api/select_files", query_string={"project_path": str(project)}).get_json()
    assert files["safe"] == ["main.py"]

    start = client.post("/api/pack", json={
        "project_path": str(project), "output_path": str(output_path), "selected_files": files["safe"],
    })
    assert start.status_code == 200
    job_id = start.get_json()["job_id"]

    status = _wait_for_job(client, job_id)
    assert status["state"] == "reviewing"
    assert not output_path.exists()  # paused for review, nothing saved yet

    review = client.get("/api/pack/review", query_string={"job_id": job_id})
    assert review.status_code == 200
    review_data = review.get_json()
    assert review_data["project"]["name"] == "project"
    all_reviewed = review_data["needs_review"] + review_data["auto_kept"]
    assert [e["file"] for e in all_reviewed] == ["main.py"]

    finalize = client.post("/api/pack/finalize", json={
        "job_id": job_id,
        "project_name": "",  # blank -> keep as-is, same as pressing enter through a terminal prompt
        "rules": review_data["rules"],
        "summaries": {"main.py": "Adds two numbers."},
    })
    assert finalize.status_code == 200
    assert finalize.get_json() == {"aif_path": str(output_path), "project_path": str(project)}
    assert output_path.exists()

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["files"]["main.py"]["summary"] == "Adds two numbers."

    # the review payload is gone once a job is finalized -- nothing left to review
    assert client.get("/api/pack/review", query_string={"job_id": job_id}).status_code == 404


def test_api_pack_no_llm_flag_reaches_pack_service(client, tmp_path, monkeypatch):
    # Route-level check that the "no_llm" checkbox's value actually reaches
    # pack_service.start_pack_job() -- pack_service.py's own test suite
    # covers the resulting structural-summary behavior in detail.
    class _RaisingProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            raise AssertionError("no_llm=True must never call the LLM provider")

    monkeypatch.setattr(llm, "_provider", _RaisingProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    start = client.post("/api/pack", json={
        "project_path": str(project), "selected_files": ["main.py"], "no_llm": True,
    })
    job_id = start.get_json()["job_id"]

    status = _wait_for_job(client, job_id)
    assert status["state"] == "reviewing"
    review = client.get("/api/pack/review", query_string={"job_id": job_id}).get_json()
    assert review["project"]["prompt"] == packager.STRUCTURAL_ONLY_NOTE["en"]


def test_api_pack_lang_flag_reaches_pack_service(client, tmp_path, monkeypatch):
    # Route-level check that the pack form's "lang" value actually reaches
    # packager.pack() and lands in the saved aif -- llm.py's own tests cover
    # the prompt-construction detail, pack_service.py's the job-state
    # plumbing; this just confirms the route wires them together.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    start = client.post("/api/pack", json={
        "project_path": str(project), "selected_files": ["main.py"], "lang": "ko",
    })
    job_id = start.get_json()["job_id"]

    status = _wait_for_job(client, job_id)
    assert status["state"] == "reviewing"
    review = client.get("/api/pack/review", query_string={"job_id": job_id}).get_json()
    assert review["project"]["language"] == "ko"


def test_api_pack_unrecognized_lang_falls_back_to_english(client, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    start = client.post("/api/pack", json={
        "project_path": str(project), "selected_files": ["main.py"], "lang": "fr",
    })
    job_id = start.get_json()["job_id"]

    status = _wait_for_job(client, job_id)
    review = client.get("/api/pack/review", query_string={"job_id": job_id}).get_json()
    assert review["project"]["language"] == "en"


def test_api_pack_status_retry_params_can_resume_a_failed_job_over_http(client, tmp_path, monkeypatch):
    # Item 8: the pack-progress screen used to have no way forward at all
    # once a job landed in "error" (a repeated LLM failure) -- retry_params
    # in /api/pack/status is what js/pack.js's retry button reposts to
    # /api/pack, and this is the route-level proof that round-trip actually
    # resumes from the checkpoint instead of starting over. pack_service.py's
    # own test suite covers the same behavior below the HTTP layer.
    class _EmptyRulesProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            if '"rules"' in prompt:
                return "{}"
            return super().generate(prompt, retry=retry)

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    monkeypatch.setattr(llm, "_provider", _EmptyRulesProvider())
    start = client.post("/api/pack", json={"project_path": str(project), "selected_files": ["main.py"]})
    job_id = start.get_json()["job_id"]
    status = _wait_for_job(client, job_id)
    assert status["state"] == "error"
    retry_params = status["retry_params"]
    assert retry_params["project_path"] == str(project)

    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    retry = client.post("/api/pack", json=retry_params)
    assert retry.status_code == 200
    retry_job_id = retry.get_json()["job_id"]
    retry_status = _wait_for_job(client, retry_job_id)
    assert retry_status["state"] == "reviewing"

    # _wait_for_job's own returned "log" is only the tail since its last
    # poll tick (it advances `since` each time, same pagination the real
    # frontend uses) -- refetch from since=0 for the full accumulated log
    # instead of misreading that tail as "nothing was restored".
    full_log = client.get(
        "/api/pack/status", query_string={"job_id": retry_job_id, "since": 0}
    ).get_json()["log"]
    assert any("체크포인트에서 복원" in line for line in full_log)


def test_api_pack_retry_with_no_cache_only_resumes_when_resume_flag_is_sent(client, tmp_path, monkeypatch):
    # Real bug reported directly: a job started with no_cache=True
    # ("완전히 재패킹" checked) that failed during rules generation still
    # checkpointed its already-generated summaries -- but retrying it
    # discarded that checkpoint outright, since no_cache=True alone made
    # packager.pack() treat any leftover checkpoint as stale regardless of
    # how fresh it actually was, re-billing every summary again on every
    # retry. /api/pack's `resume` field (only ever sent by js/pack.js's
    # retry button, never a fresh pack-form submission) is the fix -- this
    # is the route-level proof, on top of pack_service.py's own test for
    # the same fix below the HTTP layer.
    class _EmptyRulesProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            if '"rules"' in prompt:
                return "{}"
            return super().generate(prompt, retry=retry)

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    monkeypatch.setattr(llm, "_provider", _EmptyRulesProvider())
    start = client.post("/api/pack", json={
        "project_path": str(project), "selected_files": ["main.py"], "no_cache": True,
    })
    job_id = start.get_json()["job_id"]
    status = _wait_for_job(client, job_id)
    assert status["state"] == "error"
    retry_params = status["retry_params"]
    assert retry_params["no_cache"] is True

    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    retry = client.post("/api/pack", json={**retry_params, "resume": True})
    assert retry.status_code == 200
    retry_job_id = retry.get_json()["job_id"]
    retry_status = _wait_for_job(client, retry_job_id)
    assert retry_status["state"] == "reviewing"

    full_log = client.get(
        "/api/pack/status", query_string={"job_id": retry_job_id, "since": 0}
    ).get_json()["log"]
    assert any("체크포인트에서 복원" in line for line in full_log)


def test_api_pack_link_adds_an_edge_and_rejects_cycles(client, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
    (project / "b.py").write_text("def b():\n    pass\n", encoding="utf-8")

    start = client.post("/api/pack", json={"project_path": str(project), "selected_files": ["a.py", "b.py"]})
    job_id = start.get_json()["job_id"]
    _wait_for_job(client, job_id)

    link = client.post("/api/pack/link", json={"job_id": job_id, "file": "a.py", "target": "b.py"})
    assert link.status_code == 200
    assert link.get_json()["tree"]["a.py"]["internal"] == ["b.py"]

    cycle = client.post("/api/pack/link", json={"job_id": job_id, "file": "b.py", "target": "a.py"})
    assert cycle.status_code == 409
    assert "error" in cycle.get_json()

    missing = client.post("/api/pack/link", json={"job_id": "no-such-job", "file": "a.py", "target": "b.py"})
    assert missing.status_code == 404


def test_api_pack_unlink_removes_only_that_edge(client, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
    (project / "b.py").write_text("def b():\n    pass\n", encoding="utf-8")
    (project / "c.py").write_text("def c():\n    pass\n", encoding="utf-8")

    start = client.post("/api/pack", json={"project_path": str(project), "selected_files": ["a.py", "b.py", "c.py"]})
    job_id = start.get_json()["job_id"]
    _wait_for_job(client, job_id)

    client.post("/api/pack/link", json={"job_id": job_id, "file": "a.py", "target": "b.py"})
    client.post("/api/pack/link", json={"job_id": job_id, "file": "a.py", "target": "c.py"})

    unlink = client.post("/api/pack/unlink", json={"job_id": job_id, "file": "a.py", "target": "b.py"})
    assert unlink.status_code == 200
    assert unlink.get_json()["tree"]["a.py"]["internal"] == ["c.py"]

    missing = client.post("/api/pack/unlink", json={"job_id": "no-such-job", "file": "a.py", "target": "b.py"})
    assert missing.status_code == 404


def test_api_pack_cancel_discards_a_reviewing_job(client, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    start = client.post("/api/pack", json={"project_path": str(project), "selected_files": ["main.py"]})
    job_id = start.get_json()["job_id"]
    _wait_for_job(client, job_id)

    cancel = client.post("/api/pack/cancel", json={"job_id": job_id})
    assert cancel.status_code == 200
    assert cancel.get_json() == {"ok": True}

    status = client.get("/api/pack/status", query_string={"job_id": job_id}).get_json()
    assert status["state"] == "error"


def test_api_pack_stop_unknown_job_is_404(client):
    res = client.post("/api/pack/stop", json={"job_id": "no-such-job", "save": True})
    assert res.status_code == 404


class _BlockingProvider(llm.MockProvider):
    """Blocks generate() until the test releases it -- MockProvider alone
    answers instantly, so a real pack job would reach "reviewing" before a
    test could ever hit /api/pack/stop while it's genuinely still "running".
    """

    def __init__(self, started: threading.Event, release: threading.Event):
        self.started = started
        self.release = release

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        self.started.set()
        self.release.wait(timeout=5)
        return super().generate(prompt, retry)


def test_api_pack_stop_saves_and_stops_a_running_job(client, tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(llm, "_provider", _BlockingProvider(started, release))
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    start = client.post("/api/pack", json={"project_path": str(project), "selected_files": ["main.py"]})
    job_id = start.get_json()["job_id"]
    assert started.wait(timeout=5), "generate() was never entered"

    stop = client.post("/api/pack/stop", json={"job_id": job_id, "save": True})
    assert stop.status_code == 200
    assert stop.get_json() == {"ok": True}

    release.set()
    status = _wait_for_job(client, job_id)

    assert status["state"] == "error"
    assert "체크포인트에 저장됨" in status["error"]
    assert checkpoint._checkpoint_path(str(project)).exists()


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
    assert res.get_json() == {
        "a.py": {"summary": "does a thing", "confidence": 1.0},
        "b.py": {"summary": "uses a.py", "confidence": 1.0},
    }


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
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": ["a.py"], "external": []},
    }


def test_api_relationships_link(client, tmp_path):
    # _write_sample_aif already has b.py -> a.py, so link a.py -> b.py here
    # would close a cycle (see test_api_relationships_link_rejects_a_cycle
    # below) -- unlink that edge first so this test exercises a plain link.
    aif_path = _write_sample_aif(tmp_path)
    client.post("/api/relationships/unlink", json={"aif_path": aif_path, "file": "b.py", "target": "a.py"})

    res = client.post("/api/relationships/link", json={"aif_path": aif_path, "file": "a.py", "target": "b.py"})
    assert res.status_code == 200
    assert res.get_json()["relationships"]["a.py"]["internal"] == ["b.py"]

    # persisted -- a second, independent read sees the same edit
    reread = client.get("/api/relationships", query_string={"aif_path": aif_path})
    assert reread.get_json()["a.py"]["internal"] == ["b.py"]


def test_api_relationships_link_rejects_a_cycle(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)  # b.py already depends on a.py
    res = client.post("/api/relationships/link", json={"aif_path": aif_path, "file": "a.py", "target": "b.py"})
    assert res.status_code == 409


def test_api_relationships_link_requires_all_fields(client):
    res = client.post("/api/relationships/link", json={"aif_path": "x.json"})
    assert res.status_code == 400


def test_api_relationships_unlink(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.post("/api/relationships/unlink", json={"aif_path": aif_path, "file": "b.py", "target": "a.py"})
    assert res.status_code == 200
    assert res.get_json()["relationships"]["b.py"]["internal"] == []


def test_api_blast_radius(client, tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    res = client.get("/api/blast_radius", query_string={"aif_path": aif_path, "file": "a.py"})
    assert res.get_json() == ["b.py"]


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
        "is_stale": False, "changed": [], "added": [], "removed": [], "unchanged": ["a.py"],
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
    SAVE = "save"


class _FakeWindow:
    def __init__(self, dialog_result=None, confirm_result=None):
        self._dialog_result = dialog_result
        self.confirm_calls = []
        self._confirm_result = confirm_result
        self.create_file_dialog_calls = []

    def create_file_dialog(self, *args, **kwargs):
        self.create_file_dialog_calls.append((args, kwargs))
        return self._dialog_result

    def create_confirmation_dialog(self, title, message):
        self.confirm_calls.append((title, message))
        return self._confirm_result


class _FakeWebview:
    FileDialog = _FakeFileDialog

    def __init__(self, window):
        self.windows = [window]


# _Api and _confirm_close_if_reviewing used to be nested inside main() --
# defined at module level now specifically so they're reachable like this,
# with a fake `webview` module standing in for the real one (which needs an
# actual OS window and can't run in a test process).

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


def test_api_choose_save_file_uses_a_save_dialog():
    window = _FakeWindow(dialog_result=["C:/some/new.json"])
    api = gui_server._Api(_FakeWebview(window))
    assert api.choose_save_file() == "C:/some/new.json"
    args, kwargs = window.create_file_dialog_calls[0]
    assert args == ("save",)
    assert kwargs["save_filename"] == "project.json"


def test_confirm_close_skips_the_dialog_when_no_job_is_reviewing(monkeypatch):
    monkeypatch.setattr(gui_server.pack_service, "has_reviewing_job", lambda: False)
    window = _FakeWindow()
    assert gui_server._confirm_close_if_reviewing(window) is None
    assert window.confirm_calls == []


def test_confirm_close_shows_a_native_dialog_when_a_job_is_reviewing(monkeypatch):
    monkeypatch.setattr(gui_server.pack_service, "has_reviewing_job", lambda: True)
    window = _FakeWindow(confirm_result=False)  # user chose "cancel the close"
    result = gui_server._confirm_close_if_reviewing(window)
    assert result is False
    assert len(window.confirm_calls) == 1
