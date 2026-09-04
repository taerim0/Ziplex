"""Unit tests for pack_service.py's job lifecycle -- file listing, start/
poll/log capture, the reviewing pause, and submit/cancel -- independent of
gui_server.py's routes (those get their own thin adapter tests in
test_gui_server.py, same split as test_mcp_server.py/test_relationship.py).
Uses llm.MockProvider so these run network-free, same pattern as
test_pack_integration.py.
"""

import json
import threading
import time

import pytest

from ziplex import checkpoint
from ziplex import llm
from ziplex import packager
from ziplex import settings as app_settings
from ziplex.gui import pack_service
from ziplex.file.relationship import CycleError


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _wait(job_id, timeout=10):
    """Waits for a job to leave "running" -- into "reviewing" (the normal
    happy path now that packing always pauses for review) or "error".
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = pack_service.get_job_status(job_id)
        if status["state"] != "running":
            return status
        time.sleep(0.02)
    raise AssertionError("pack job did not finish in time")


def test_get_job_status_unknown_job_is_none():
    assert pack_service.get_job_status("no-such-job") is None


def test_list_selectable_files_splits_safe_and_dangerous(tmp_path):
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "secret.env", 'API_KEY = "abc123"\n')

    result = pack_service.list_selectable_files(str(project))

    assert "main.py" in result["safe"]
    dangerous_names = [d["file"] for d in result["dangerous"]]
    assert dangerous_names == ["secret.env"]
    assert result["dangerous"][0]["matched_text"] == 'API_KEY = "abc123"'


def test_list_selectable_files_default_output_path_empty_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    result = pack_service.list_selectable_files(str(project))

    assert result["default_output_path"] == ""


def test_list_selectable_files_default_output_path_follows_global_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    app_settings.save_settings({"output_dir": str(tmp_path / "out"), "project_output_dirs": {}})
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    result = pack_service.list_selectable_files(str(project))

    assert result["default_output_path"] == str(tmp_path / "out" / "project.json")


def test_start_pack_job_resolves_blank_output_path_from_settings_default(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    app_settings.save_settings({"output_dir": str(tmp_path / "out"), "project_output_dirs": {}})

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    # output_path left None (the pack form's field left blank) -- resolved
    # via settings.py before the job dict is even built, so this is visible
    # immediately, no need to wait for the background thread.
    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])

    job = pack_service._lookup_job(job_id)
    assert job["output_path"] == str(tmp_path / "out" / "project.json")
    _wait(job_id)  # let the thread finish so it doesn't outlive the test


def test_start_pack_job_pins_project_when_explicit_output_path_given(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    output_path = tmp_path / "custom" / "project.json"

    job_id = pack_service.start_pack_job(str(project), str(output_path), selected_files=["main.py"])
    _wait(job_id)

    pinned = app_settings.load_settings()["project_output_dirs"]
    assert pinned[str(project.resolve())] == str(output_path.parent)


def test_start_pack_job_does_not_pin_when_output_path_left_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    _wait(job_id)

    # leaving the field blank must never create a pin -- otherwise a
    # project would silently freeze onto RESULT_DIR (or whatever the
    # global default happened to be) the very first time it's packed,
    # instead of continuing to track later changes to that default
    assert app_settings.load_settings()["project_output_dirs"] == {}


class _EmptyRulesProvider(llm.MockProvider):
    """Same idea as test_pack_integration.py's own _EmptyRulesProvider --
    duplicated here rather than imported, since test modules in this suite
    don't import fixtures from one another. Answers everything normally
    except a rules-shaped prompt, which always comes back empty --
    packager.pack()'s `while not rules` loop then keeps hitting
    checkpoint.handle_llm_failure(), which under pack_service.py's always-
    non-interactive pack() call checkpoints and stops (job ends up in state
    "error"), exactly the scenario item 8's retry button exists for.
    """

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        if '"rules"' in prompt:
            return "{}"
        return super().generate(prompt, retry=retry)


def test_get_job_status_includes_retry_params(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    output_path = tmp_path / "out" / "project.json"

    job_id = pack_service.start_pack_job(
        str(project), str(output_path), no_cache=True, no_llm=False, selected_files=["main.py"]
    )
    status = pack_service.get_job_status(job_id)

    assert status["retry_params"] == {
        "project_path": str(project),
        "output_path": str(output_path),
        "no_cache": True,
        "no_llm": False,
        "selected_files": ["main.py"],
        "lang": "en",
        "progress_lang": "ko",
    }
    _wait(job_id)


def test_get_job_status_retry_params_output_path_stays_blank_when_left_blank(tmp_path, monkeypatch):
    # Regression: start_pack_job() resolves a blank output_path into a
    # concrete one internally (settings.py) before ever calling pack() --
    # retry_params must echo back the *original* blank, not that resolved
    # path, or reposting it on retry would look like an explicit choice and
    # silently pin the project (see start_pack_job()'s own set_project_
    # output_dir() call) even though nothing was ever typed. A global
    # default is configured here specifically so the resolved output_path
    # actually diverges from the caller's blank input -- without one
    # configured, resolution stays None either way and this test can't
    # tell the fixed behavior apart from the bug it's guarding against.
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")
    app_settings.save_settings({"output_dir": str(tmp_path / "out"), "project_output_dirs": {}})

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    status = pack_service.get_job_status(job_id)

    assert status["retry_params"]["output_path"] is None
    _wait(job_id)
    assert app_settings.load_settings()["project_output_dirs"] == {}  # confirms no pin was created


def test_retrying_a_failed_job_resumes_from_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    monkeypatch.setattr(llm, "_provider", _EmptyRulesProvider())
    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    status = _wait(job_id)
    assert status["state"] == "error"
    retry_params = pack_service.get_job_status(job_id)["retry_params"]

    # the LLM starts behaving again; reposting the exact same retry_params
    # should resume from the checkpoint handle_llm_failure() wrote, not
    # start over -- confirmed by the "restored from checkpoint" log line
    # packager.py only ever prints on that path.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    retry_job_id = pack_service.start_pack_job(**retry_params)
    retry_status = _wait(retry_job_id)

    assert retry_status["state"] == "reviewing"
    assert any("체크포인트에서 복원" in line for line in retry_status["log"])


class _EmptyRulesCountingProvider(llm.MockProvider):
    """_EmptyRulesProvider's always-fail-rules behavior plus a call counter --
    MockProvider's fixed responses can't tell a checkpoint-reuse test whether
    a summary was actually re-billed or just regenerated identically, since
    the text comes back the same either way (same reasoning as test_pack_
    integration.py's own _CountingMockProvider). A plain call count is the
    only reliable signal.
    """

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        self.calls += 1
        if '"rules"' in prompt:
            return "{}"
        return super().generate(prompt, retry=retry)


class _CountingMockProvider(llm.MockProvider):
    """Plain MockProvider (rules succeed normally) plus a call counter --
    used for the "LLM starts behaving again" half of a retry test, once the
    always-fails-rules provider above has already done its job of forcing
    the initial failure/checkpoint.
    """

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        self.calls += 1
        return super().generate(prompt, retry=retry)


def test_retrying_a_full_repack_job_with_resume_does_not_rebill_summaries(tmp_path, monkeypatch):
    # Real bug reported directly: a job started with no_cache=True
    # ("완전히 재패킹" checked) that fails during rules generation
    # checkpoints its already-generated per-file summaries exactly like any
    # other job -- but retrying it used to discard that checkpoint outright,
    # since use_cache=False (from the *original* no_cache=True) made
    # packager.pack() unconditionally discard any leftover checkpoint
    # regardless of how fresh it was, re-billing every file's summary again
    # on every single retry. resume=True (only ever sent by the error
    # screen's own "다시 시도" button, see start_pack_job()'s docstring) is
    # the fix -- this proves it by call count, not just by re-checking the
    # "체크포인트에서 복원" log line test_retrying_a_failed_job_resumes_
    # from_checkpoint above already covers for the *default* no_cache=False
    # case (which never actually exercised this bug, since use_cache=True
    # there never discarded the checkpoint in the first place).
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def add(a, b):\n    return a + b\n")
    _write(project / "b.py", "def sub(a, b):\n    return a - b\n")

    provider = _EmptyRulesCountingProvider()
    monkeypatch.setattr(llm, "_provider", provider)
    job_id = pack_service.start_pack_job(str(project), no_cache=True, selected_files=["a.py", "b.py"])
    status = _wait(job_id)
    assert status["state"] == "error"
    assert provider.calls > 0  # both summaries (at least) were generated before rules failed

    retry_params = pack_service.get_job_status(job_id)["retry_params"]
    assert retry_params["no_cache"] is True  # confirms this is exactly the "완전히 재패킹" case

    # The LLM starts behaving again (rules no longer come back empty) --
    # swapped to a provider with no such failure, same as test_retrying_a_
    # failed_job_resumes_from_checkpoint above does.
    retry_provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", retry_provider)
    retry_job_id = pack_service.start_pack_job(**retry_params, resume=True)
    retry_status = _wait(retry_job_id)

    assert retry_status["state"] == "reviewing"
    assert any("체크포인트에서 복원" in line for line in retry_status["log"])
    # Only rules + prompt + folder summaries (neither of the first two had
    # succeeded yet when the checkpoint was saved, so both still need a real
    # call; folder summaries are never checkpoint-restored at all, see
    # folder_summary.py) should happen on retry -- if the checkpoint had
    # been discarded instead, this would be 5 (2 summaries re-billed + rules
    # + prompt + folder summaries), the exact bug being guarded against here.
    assert retry_provider.calls == 3


def test_fresh_pack_with_no_cache_still_discards_a_stale_leftover_checkpoint(tmp_path, monkeypatch):
    # Regression guard for the *original* fix the resume=True override above
    # must not undo: a genuinely fresh "패킹 시작" submission (resume
    # defaults False) with "완전히 재패킹" checked must still discard an
    # unrelated leftover checkpoint from some earlier, already-abandoned
    # run -- only an actual retry (resume=True) should ever preserve one.
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "a.py", "def add(a, b):\n    return a + b\n")

    # Hand-built as if left over from an unrelated earlier run of this same
    # project -- build_snapshot()'s files_data is keyed by absolute path.
    checkpoint.save_checkpoint(
        str(project),
        checkpoint.build_snapshot(project, {
            str(project / "a.py"): {
                "signatures": ["add(a, b)"], "dependencies": [], "api": [], "compressed": "", "summary": "old",
            },
        }),
    )

    job_id = pack_service.start_pack_job(str(project), no_cache=True, selected_files=["a.py"])
    status = _wait(job_id)

    assert status["state"] == "reviewing"
    assert not any("체크포인트에서 복원" in line for line in status["log"])


def test_start_pack_job_pauses_in_reviewing_state(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    output_path = tmp_path / "out" / "project.json"

    job_id = pack_service.start_pack_job(str(project), str(output_path), selected_files=["main.py"])
    status = _wait(job_id)

    assert status["state"] == "reviewing"
    assert status["result"] is None
    assert not output_path.exists()  # nothing saved until submit_review()


def test_start_pack_job_no_llm_never_calls_the_llm_and_uses_structural_summaries(tmp_path, monkeypatch):
    # A provider that raises on any call at all -- stricter than checking
    # the result afterward, since a call this test doesn't expect fails
    # immediately at the point it happens (same pattern as
    # test_pack_integration.py's use_llm=False coverage).
    class _RaisingProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            raise AssertionError("no_llm=True must never call the LLM provider")

    monkeypatch.setattr(llm, "_provider", _RaisingProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    job_id = pack_service.start_pack_job(str(project), no_llm=True, selected_files=["main.py"])
    status = _wait(job_id)

    assert status["state"] == "reviewing"
    review = pack_service.get_review(job_id)
    assert review["project"]["prompt"] == packager.STRUCTURAL_ONLY_NOTE["en"]
    assert review["rules"] == []
    auto_kept = {e["file"]: e["summary"] for e in review["auto_kept"]}
    assert auto_kept["main.py"] == "Defines: add(a, b)"


def test_start_pack_job_only_includes_selected_files(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "README.md", "# Sample\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    _wait(job_id)

    review = pack_service.get_review(job_id)
    all_files = [e["file"] for e in review["needs_review"] + review["auto_kept"]]
    assert all_files == ["main.py"]


def test_get_review_returns_none_outside_reviewing_state(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "empty_project"
    project.mkdir()
    job_id = pack_service.start_pack_job(str(project), selected_files=["nope.py"])
    status = _wait(job_id)

    assert status["state"] == "error"
    assert pack_service.get_review(job_id) is None
    assert pack_service.get_review("no-such-job") is None


def test_submit_review_applies_edits_and_finalizes(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    output_path = tmp_path / "out" / "project.json"

    job_id = pack_service.start_pack_job(str(project), str(output_path), selected_files=["main.py"])
    _wait(job_id)

    result = pack_service.submit_review(
        job_id,
        project_name="renamed-project",
        project_prompt="Custom AI guide.",
        rules=["custom rule"],
        summaries={"main.py": "Adds two numbers together."},
    )

    assert result == {"aif_path": str(output_path), "project_path": str(project)}
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["project"]["name"] == "renamed-project"
    assert saved["project"]["prompt"] == "Custom AI guide."
    assert saved["rules"] == ["custom rule"]
    assert saved["files"]["main.py"]["summary"] == "Adds two numbers together."
    # finalize_aif() prunes working-state fields from the saved output
    assert "signatures" not in saved["files"]["main.py"]
    assert "relationships" in saved

    status = pack_service.get_job_status(job_id)
    assert status["state"] == "done"
    assert status["result"] == result


def test_submit_review_keeps_unedited_fields_when_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    output_path = tmp_path / "out" / "project.json"

    job_id = pack_service.start_pack_job(str(project), str(output_path), selected_files=["main.py"])
    _wait(job_id)

    pack_service.submit_review(job_id)  # nothing changed, same as pressing enter through every prompt

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["project"]["name"] == "project"
    assert saved["project"]["prompt"] == "Mock AI guide for local testing."
    assert saved["files"]["main.py"]["summary"] == "Mock summary for local testing."


def test_submit_review_unknown_job_raises_value_error():
    try:
        pack_service.submit_review("no-such-job")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_submit_review_wrong_state_raises_value_error(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")
    project = tmp_path / "empty_project"
    project.mkdir()
    job_id = pack_service.start_pack_job(str(project), selected_files=["nope.py"])
    _wait(job_id)  # ends in "error", not "reviewing"

    try:
        pack_service.submit_review(job_id)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_cancel_job_discards_a_reviewing_job(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    output_path = tmp_path / "out" / "project.json"

    job_id = pack_service.start_pack_job(str(project), str(output_path), selected_files=["main.py"])
    _wait(job_id)

    assert pack_service.cancel_job(job_id) is True
    status = pack_service.get_job_status(job_id)
    assert status["state"] == "error"
    assert not output_path.exists()

    # cancelling again (or an unrelated job) is a no-op, not an error
    assert pack_service.cancel_job(job_id) is False
    assert pack_service.cancel_job("no-such-job") is False


def test_has_reviewing_job_reflects_job_state(tmp_path, monkeypatch):
    # Isolates this test from every other test's jobs, which otherwise
    # accumulate in the module-level `_jobs` dict for the life of the test
    # process (several other tests here deliberately leave a job sitting in
    # "reviewing") -- without this, asserting the False case would be order-
    # dependent on what ran before it.
    monkeypatch.setattr(pack_service, "_jobs", {})
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    assert pack_service.has_reviewing_job() is False

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    _wait(job_id)
    assert pack_service.has_reviewing_job() is True

    assert pack_service.cancel_job(job_id) is True
    assert pack_service.has_reviewing_job() is False


def test_start_pack_job_evicts_oldest_finished_jobs_past_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(pack_service, "_jobs", {})
    monkeypatch.setattr(pack_service, "_MAX_FINISHED_JOBS", 2)
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    job_ids = []
    for _ in range(4):
        job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
        _wait(job_id)
        pack_service.cancel_job(job_id)  # -> "error" state, i.e. "finished"
        job_ids.append(job_id)

    # cap is 2, and starting a new job also runs eviction -- by the time the
    # 4th job is inserted, 3 finished jobs already exist ahead of it, so the
    # oldest (job_ids[0]) should have fallen off.
    assert pack_service.get_job_status(job_ids[0]) is None
    assert pack_service.get_job_status(job_ids[1]) is not None
    assert pack_service.get_job_status(job_ids[2]) is not None
    assert pack_service.get_job_status(job_ids[3]) is not None


def test_review_includes_a_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def a():\n    pass\n")
    _write(project / "b.py", "def b():\n    pass\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["a.py", "b.py"])
    _wait(job_id)

    review = pack_service.get_review(job_id)
    # neither file imports the other -- both start as roots, no internal edges
    assert review["tree"]["a.py"] == {"internal": [], "external": [], "internal_text_refs": []}
    assert review["tree"]["b.py"] == {"internal": [], "external": [], "internal_text_refs": []}


def test_review_includes_every_folder_untriaged(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    _wait(job_id)

    review = pack_service.get_review(job_id)
    assert [f["folder"] for f in review["folders"]] == ["."]
    assert review["folders"][0]["summary"]


def test_submit_review_applies_folder_summary_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    output_path = tmp_path / "out" / "project.json"

    job_id = pack_service.start_pack_job(str(project), str(output_path), selected_files=["main.py"])
    _wait(job_id)

    pack_service.submit_review(job_id, folder_summaries={".": "Custom folder summary."})

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["folders"]["."]["summary"] == "Custom folder summary."


def test_add_dependency_in_job_links_and_returns_updated_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def a():\n    pass\n")
    _write(project / "b.py", "def b():\n    pass\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["a.py", "b.py"])
    _wait(job_id)

    tree = pack_service.add_dependency_in_job(job_id, "a.py", "b.py")
    assert tree["a.py"]["internal"] == ["b.py"]

    # get_review() (and a later finalize) sees the same edit, not just the
    # return value of add_dependency_in_job() itself
    assert pack_service.get_review(job_id)  # still reviewing, unaffected otherwise


def test_add_dependency_in_job_does_not_disturb_other_files_edges(tmp_path, monkeypatch):
    # the exact bug the earlier drag-and-drop version had: b.py is already
    # depended on by both a.py and c.py -- linking a new edge for a.py must
    # not touch c.py's own reference.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def a():\n    pass\n")
    _write(project / "b.py", "def b():\n    pass\n")
    _write(project / "c.py", "def c():\n    pass\n")
    _write(project / "d.py", "def d():\n    pass\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["a.py", "b.py", "c.py", "d.py"])
    _wait(job_id)

    pack_service.add_dependency_in_job(job_id, "a.py", "b.py")
    pack_service.add_dependency_in_job(job_id, "c.py", "b.py")

    tree = pack_service.add_dependency_in_job(job_id, "a.py", "d.py")
    assert tree["a.py"]["internal"] == ["b.py", "d.py"]
    assert tree["c.py"]["internal"] == ["b.py"]  # untouched


def test_add_dependency_in_job_rejects_a_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def a():\n    pass\n")
    _write(project / "b.py", "def b():\n    pass\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["a.py", "b.py"])
    _wait(job_id)

    pack_service.add_dependency_in_job(job_id, "a.py", "b.py")  # a -> b

    try:
        pack_service.add_dependency_in_job(job_id, "b.py", "a.py")  # would close a -> b -> a
        assert False, "expected CycleError"
    except CycleError:
        pass


def test_remove_dependency_in_job_unlinks_only_that_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def a():\n    pass\n")
    _write(project / "b.py", "def b():\n    pass\n")
    _write(project / "c.py", "def c():\n    pass\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["a.py", "b.py", "c.py"])
    _wait(job_id)

    pack_service.add_dependency_in_job(job_id, "a.py", "b.py")
    pack_service.add_dependency_in_job(job_id, "a.py", "c.py")

    tree = pack_service.remove_dependency_in_job(job_id, "a.py", "b.py")
    assert tree["a.py"]["internal"] == ["c.py"]


def test_get_review_reflects_edits_made_via_add_dependency_in_job(tmp_path, monkeypatch):
    # Regression test: get_review() used to return a snapshot cached once at
    # job-pause time, so a second fetch (e.g. a page reload) after a link/
    # unlink edit would show a stale relationship graph even though the edit
    # was already applied to the job's real aif and would be what actually
    # gets saved.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def a():\n    pass\n")
    _write(project / "b.py", "def b():\n    pass\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["a.py", "b.py"])
    _wait(job_id)

    assert pack_service.get_review(job_id)["tree"]["a.py"]["internal"] == []

    pack_service.add_dependency_in_job(job_id, "a.py", "b.py")

    assert pack_service.get_review(job_id)["tree"]["a.py"]["internal"] == ["b.py"]


def test_add_dependency_in_job_after_cancel_raises_value_error_not_crash(tmp_path, monkeypatch):
    # Regression test: cancel_job() sets job["aif"] = None; a mutation that
    # had already fetched the job but not yet re-checked for None used to
    # dereference it directly (job["aif"]["files"]) and blow up with a raw
    # TypeError instead of the same clean ValueError every other invalid-
    # state case raises.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def a():\n    pass\n")
    _write(project / "b.py", "def b():\n    pass\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["a.py", "b.py"])
    _wait(job_id)
    pack_service.cancel_job(job_id)

    try:
        pack_service.add_dependency_in_job(job_id, "a.py", "b.py")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_cancel_job_after_submit_review_is_a_noop(tmp_path, monkeypatch):
    # Regression test: cancel_job() and submit_review() used to both be able
    # to act on a job in state "reviewing" independently, so a cancel racing
    # a submit that had already started committing could flip a "done" job
    # back to "error" after its output was already written to disk.
    # submit_review() now moves the job out of "reviewing" (into
    # "finalizing", then "done") before doing any of that I/O, so a cancel
    # arriving anytime after submit_review() has been called at all is
    # rejected instead of silently reverting an already-saved result.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def a():\n    pass\n")
    output_path = tmp_path / "out" / "project.json"

    job_id = pack_service.start_pack_job(str(project), str(output_path), selected_files=["a.py"])
    _wait(job_id)
    pack_service.submit_review(job_id)

    assert pack_service.cancel_job(job_id) is False
    status = pack_service.get_job_status(job_id)
    assert status["state"] == "done"
    assert output_path.exists()


def test_add_dependency_in_job_unknown_job_raises_value_error():
    try:
        pack_service.add_dependency_in_job("no-such-job", "a.py", "b.py")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_remove_dependency_in_job_unknown_job_raises_value_error():
    try:
        pack_service.remove_dependency_in_job("no-such-job", "a.py", "b.py")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_add_dependency_in_job_persists_into_the_finalized_output(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "a.py", "def a():\n    pass\n")
    _write(project / "b.py", "def b():\n    pass\n")
    output_path = tmp_path / "out" / "project.json"

    job_id = pack_service.start_pack_job(str(project), str(output_path), selected_files=["a.py", "b.py"])
    _wait(job_id)

    pack_service.add_dependency_in_job(job_id, "a.py", "b.py")
    pack_service.submit_review(job_id)

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["relationships"]["a.py"]["internal"] == ["b.py"]


def test_concurrent_jobs_do_not_cross_contaminate_logs(tmp_path, monkeypatch):
    # Regression test: _run()/submit_review() used to capture print() output
    # via a bare contextlib.redirect_stdout(...), which reassigns the single
    # process-wide sys.stdout with no locking -- two jobs' captured blocks
    # overlapping in time (exactly what running two packs at once does)
    # could each clobber the other's redirect, so one job's progress lines
    # could end up appended to a different job's log instead of its own.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project_a = tmp_path / "project_alpha"
    _write(project_a / "alpha_only.py", "def alpha():\n    pass\n")
    project_b = tmp_path / "project_beta"
    _write(project_b / "beta_only.py", "def beta():\n    pass\n")

    job_a = pack_service.start_pack_job(str(project_a), selected_files=["alpha_only.py"])
    job_b = pack_service.start_pack_job(str(project_b), selected_files=["beta_only.py"])
    status_a = _wait(job_a)
    status_b = _wait(job_b)

    log_a = "\n".join(pack_service.get_job_status(job_a)["log"])
    log_b = "\n".join(pack_service.get_job_status(job_b)["log"])

    assert status_a["state"] == "reviewing"
    assert status_b["state"] == "reviewing"
    assert "alpha_only.py" in log_a and "beta_only.py" not in log_a
    assert "beta_only.py" in log_b and "alpha_only.py" not in log_b


def test_get_job_status_since_returns_only_new_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    _wait(job_id)

    full = pack_service.get_job_status(job_id)
    assert full["log_len"] == len(full["log"])

    tail = pack_service.get_job_status(job_id, since=full["log_len"])
    assert tail["log"] == []
    assert tail["log_len"] == full["log_len"]


def test_pack_job_on_empty_project_reports_error_state(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "empty_project"
    project.mkdir()  # no files at all -> pack() selects nothing and returns {}

    job_id = pack_service.start_pack_job(str(project), selected_files=["whatever.py"])
    status = _wait(job_id)

    assert status["state"] == "error"
    assert status["result"] is None
    assert status["error"]


def _write_saved_aif(path, relationships):
    """A minimal already-finalized aif.json -- no `dependencies`/`compressed`
    on any file, matching what a real pack() run leaves behind after
    finalize_aif()/save_aif() split those out. link_saved_relationship()/
    unlink_saved_relationship() operate on exactly this on-disk shape.
    """
    path.write_text(json.dumps({
        "project": {"name": "sample", "prompt": "A sample project."},
        "rules": [],
        "tokens": {},
        "files": {name: {"summary": "x", "confidence": 1.0} for name in relationships},
        "relationships": relationships,
    }), encoding="utf-8")


def test_link_saved_relationship_edits_the_file_directly(tmp_path):
    aif_path = tmp_path / "sample.json"
    _write_saved_aif(aif_path, {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": [], "external": []},
    })

    relationships = pack_service.link_saved_relationship(str(aif_path), "a.py", "b.py")
    assert relationships["a.py"]["internal"] == ["b.py"]

    # persisted, not just returned
    saved = json.loads(aif_path.read_text(encoding="utf-8"))
    assert saved["relationships"]["a.py"]["internal"] == ["b.py"]
    assert saved["project"]["name"] == "sample"  # everything else untouched


def test_link_saved_relationship_does_not_touch_sibling_detail_or_cache_files(tmp_path):
    # the exact bug this function's docstring warns against: reusing
    # packager.save_aif() here would blank out detail.json/cache.json since
    # neither `compressed` nor `_manifest` exist on an already-saved aif.
    aif_path = tmp_path / "sample.json"
    _write_saved_aif(aif_path, {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": [], "external": []},
    })
    detail_path = tmp_path / "sample.detail.json"
    cache_path = tmp_path / "sample.cache.json"
    detail_path.write_text(json.dumps({"a.py": {"compressed": "real body"}}), encoding="utf-8")
    cache_path.write_text(json.dumps({"a.py": "somehash"}), encoding="utf-8")

    pack_service.link_saved_relationship(str(aif_path), "a.py", "b.py")

    assert json.loads(detail_path.read_text(encoding="utf-8")) == {"a.py": {"compressed": "real body"}}
    assert json.loads(cache_path.read_text(encoding="utf-8")) == {"a.py": "somehash"}


def test_link_saved_relationship_rejects_a_cycle(tmp_path):
    aif_path = tmp_path / "sample.json"
    _write_saved_aif(aif_path, {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": ["a.py"], "external": []},  # b already depends on a
    })

    with pytest.raises(CycleError):
        pack_service.link_saved_relationship(str(aif_path), "a.py", "b.py")  # would close a -> b -> a


def test_link_saved_relationship_raises_on_unknown_file(tmp_path):
    aif_path = tmp_path / "sample.json"
    _write_saved_aif(aif_path, {"a.py": {"internal": [], "external": []}})

    with pytest.raises(ValueError):
        pack_service.link_saved_relationship(str(aif_path), "a.py", "missing.py")


def test_unlink_saved_relationship_removes_only_that_edge(tmp_path):
    aif_path = tmp_path / "sample.json"
    _write_saved_aif(aif_path, {
        "a.py": {"internal": ["b.py", "c.py"], "external": []},
        "b.py": {"internal": [], "external": []},
        "c.py": {"internal": [], "external": []},
    })

    relationships = pack_service.unlink_saved_relationship(str(aif_path), "a.py", "b.py")
    assert relationships["a.py"]["internal"] == ["c.py"]

    saved = json.loads(aif_path.read_text(encoding="utf-8"))
    assert saved["relationships"]["a.py"]["internal"] == ["c.py"]


def test_lock_for_path_returns_the_same_lock_for_equivalent_paths(tmp_path):
    aif_path = tmp_path / "sample.json"
    aif_path.write_text("{}", encoding="utf-8")

    absolute = pack_service._lock_for_path(str(aif_path))
    relative_equivalent = pack_service._lock_for_path(str(aif_path.parent) + f"/./{aif_path.name}")
    assert absolute is relative_equivalent


def test_lock_for_path_returns_different_locks_for_different_paths(tmp_path):
    a = pack_service._lock_for_path(str(tmp_path / "a.json"))
    b = pack_service._lock_for_path(str(tmp_path / "b.json"))
    assert a is not b


def test_lock_for_path_normalizes_case_for_a_not_yet_existing_file(tmp_path):
    # Found by code review: Path.resolve() only normalizes an existing
    # path's real on-disk case on Windows -- "Out.json" and "out.json"
    # resolve to *different* strings before the file is ever created (only
    # converging afterward), which would silently give two different Lock
    # objects for what's actually the same eventual file, right at the
    # exact moment (a brand-new file) this locking matters most.
    aif_path = tmp_path / "Out.json"
    assert not aif_path.exists()

    upper = pack_service._lock_for_path(str(aif_path))
    lower = pack_service._lock_for_path(str(tmp_path / "out.json"))
    assert upper is lower


def test_lock_for_path_evicts_old_unlocked_entries_past_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(pack_service, "_file_locks", {})
    monkeypatch.setattr(pack_service, "_MAX_FILE_LOCKS", 3)

    first = pack_service._lock_for_path(str(tmp_path / "a.json"))
    pack_service._lock_for_path(str(tmp_path / "b.json"))
    pack_service._lock_for_path(str(tmp_path / "c.json"))
    assert len(pack_service._file_locks) == 3

    # past the cap -- "a" is the oldest and unlocked, so it's the one evicted
    pack_service._lock_for_path(str(tmp_path / "d.json"))
    assert len(pack_service._file_locks) == 3
    assert pack_service._lock_for_path(str(tmp_path / "a.json")) is not first


def test_lock_for_path_never_evicts_a_currently_held_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(pack_service, "_file_locks", {})
    monkeypatch.setattr(pack_service, "_MAX_FILE_LOCKS", 1)

    held = pack_service._lock_for_path(str(tmp_path / "held.json"))
    held.acquire()
    try:
        # every request past the cap tries to evict "held.json" first (it's
        # the oldest) -- since it's locked, eviction must skip it instead of
        # silently handing a second caller a different Lock for the same path
        again = pack_service._lock_for_path(str(tmp_path / "held.json"))
        assert again is held
        pack_service._lock_for_path(str(tmp_path / "other.json"))
        assert pack_service._lock_for_path(str(tmp_path / "held.json")) is held
    finally:
        held.release()


def test_link_saved_relationship_is_safe_under_concurrent_writes(tmp_path):
    # Found by code review: link_saved_relationship()'s read-modify-write
    # had no guard against two calls racing on the same file, unlike every
    # job mutation in this module (see _lock_for_path()'s own comment).
    # Two threads each add a *different* edge on the *same* aif.json,
    # repeatedly -- without the lock, a lost update (one thread's write
    # clobbering the other's read-modify-write in flight) would eventually
    # drop one side's edge. Repeats several rounds since a race is
    # timing-dependent and might not surface on a single attempt.
    aif_path = tmp_path / "sample.json"

    for round_num in range(20):
        _write_saved_aif(aif_path, {
            "a.py": {"internal": [], "external": []},
            "b.py": {"internal": [], "external": []},
            "c.py": {"internal": [], "external": []},
        })

        errors = []

        def link_b():
            try:
                pack_service.link_saved_relationship(str(aif_path), "a.py", "b.py")
            except Exception as e:  # pragma: no cover -- surfaced via `errors`
                errors.append(e)

        def link_c():
            try:
                pack_service.link_saved_relationship(str(aif_path), "a.py", "c.py")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        t1, t2 = threading.Thread(target=link_b), threading.Thread(target=link_c)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"round {round_num}: {errors}"
        saved = json.loads(aif_path.read_text(encoding="utf-8"))
        assert sorted(saved["relationships"]["a.py"]["internal"]) == ["b.py", "c.py"], (
            f"round {round_num}: lost an update -- {saved['relationships']['a.py']}"
        )


def test_request_cancel_unknown_job_returns_false():
    assert pack_service.request_cancel("no-such-job", save=True) is False


def test_request_cancel_returns_false_for_a_reviewing_job(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    _wait(job_id)  # now "reviewing" -- nothing left running to stop

    assert pack_service.request_cancel(job_id, save=True) is False


class _BlockingProvider(llm.MockProvider):
    """Blocks generate() until the test releases it -- MockProvider alone
    answers instantly, so a real pack job would reach "reviewing" before a
    test could ever call request_cancel() while it's genuinely still
    "running". `started` is set the moment generate() is actually entered,
    so the test can wait for that instead of guessing with a sleep loop.
    """

    def __init__(self, started: threading.Event, release: threading.Event):
        self.started = started
        self.release = release

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        self.started.set()
        self.release.wait(timeout=5)
        return super().generate(prompt, retry)


def test_request_cancel_save_stops_a_running_job_and_checkpoints_it(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(llm, "_provider", _BlockingProvider(started, release))
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    assert started.wait(timeout=5), "generate() was never entered"
    assert pack_service.get_job_status(job_id)["state"] == "running"

    assert pack_service.request_cancel(job_id, save=True) is True

    # Lets the blocked summary call finish -- pack()'s third checkpoint
    # (right after the summary step) is what actually consumes the cancel
    # request and stops it, before rules/prompt would call generate() again.
    release.set()
    status = _wait(job_id)

    assert status["state"] == "error"
    assert "체크포인트에 저장됨" in status["error"]
    assert checkpoint._checkpoint_path(str(project)).exists()


def test_request_cancel_discard_stops_a_running_job_without_a_checkpoint(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(llm, "_provider", _BlockingProvider(started, release))
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "checkpoint")

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    job_id = pack_service.start_pack_job(str(project), selected_files=["main.py"])
    assert started.wait(timeout=5), "generate() was never entered"

    assert pack_service.request_cancel(job_id, save=False) is True

    release.set()
    status = _wait(job_id)

    assert status["state"] == "error"
    assert "저장하지 않음" in status["error"]
    assert not checkpoint._checkpoint_path(str(project)).exists()
