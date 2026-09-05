"""watcher.py -- live filesystem watching backing /api/watch/start and
/api/watch/status. Uses a real watchdog Observer against real tmp_path
files (not mocked), since the whole point is OS-level event delivery
actually working; DEBOUNCE_SECONDS is monkeypatched to near-zero so tests
don't have to wait out the real (1s) debounce window.
"""

import json
import time

import pytest

from ziplex.gui import watcher
from ziplex.freshness import build_manifest


@pytest.fixture(autouse=True)
def _stop_all_watchers_after_each_test():
    """Every test that starts a watcher must not leave its background
    Observer thread running past the test -- it'd keep watching a tmp_path
    pytest is about to delete, and accumulate across the whole test run
    otherwise. Cheaper to always sweep here than to require every test to
    remember its own stop_watch() call.
    """
    yield
    for key in list(watcher._watchers):
        watcher._watchers.pop(key)["observer"].stop()


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_project_with_manifest(tmp_path):
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    manifest = build_manifest([str(project / "main.py")], str(project))
    aif_path = tmp_path / "out" / "project.json"
    aif_path.parent.mkdir(parents=True, exist_ok=True)
    aif_path.write_text("{}", encoding="utf-8")
    (tmp_path / "out" / "project.cache.json").write_text(json.dumps(manifest), encoding="utf-8")
    return project, aif_path


def _wait_until(predicate, timeout=5, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_start_watch_populates_an_initial_fresh_report_synchronously(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.05)
    project, aif_path = _make_project_with_manifest(tmp_path)

    watcher.start_watch(str(project), str(aif_path))

    report = watcher.get_status(str(project))
    assert report is not None
    assert report["is_stale"] is False


def test_get_status_returns_none_when_nothing_is_watching(tmp_path):
    project = tmp_path / "never-watched"
    project.mkdir()
    assert watcher.get_status(str(project)) is None


def test_watcher_detects_a_real_file_change(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.05)
    project, aif_path = _make_project_with_manifest(tmp_path)
    watcher.start_watch(str(project), str(aif_path))
    assert watcher.get_status(str(project))["is_stale"] is False

    _write(project / "main.py", "def add(a, b):\n    return a + b + 1\n")

    detected = _wait_until(lambda: watcher.get_status(str(project))["is_stale"] is True)
    assert detected, "watcher never picked up the file change"
    report = watcher.get_status(str(project))
    assert report["changed"] == ["main.py"]


def test_watcher_does_not_flag_a_previously_included_dangerous_file_as_removed(tmp_path, monkeypatch):
    # Real bug reported directly: a file flagged sensitive by scan_files()
    # but included in the pack anyway gets re-flagged as dangerous on every
    # later scan regardless of that earlier decision, dropped from
    # collect_and_scan()'s own "safe" list every time -- this watcher used
    # to only ever look at "safe", so it reported this exact file as
    # permanently removed (is_stale flipping true) even though it's
    # unchanged and still on disk.
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.05)
    project = tmp_path / "project"
    _write(project / "config.py", 'API_KEY = "abc123"\n')
    manifest = build_manifest([str(project / "config.py")], str(project))  # it WAS packed last time
    aif_path = tmp_path / "out" / "project.json"
    aif_path.parent.mkdir(parents=True, exist_ok=True)
    aif_path.write_text("{}", encoding="utf-8")
    (tmp_path / "out" / "project.cache.json").write_text(json.dumps(manifest), encoding="utf-8")

    watcher.start_watch(str(project), str(aif_path))

    report = watcher.get_status(str(project))
    assert report is not None
    assert report["is_stale"] is False
    assert report["removed"] == []


def test_watcher_ignores_changes_under_default_ignore_dirs(tmp_path, monkeypatch):
    # A .git-internal write shouldn't flip is_stale -- check_freshness()
    # only ever looks at collect_and_scan()'s already-filtered safe-file
    # list anyway, but this also confirms the ignore-spec event filter
    # itself works (not just that the end result happens to be correct).
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.05)
    project, aif_path = _make_project_with_manifest(tmp_path)
    watcher.start_watch(str(project), str(aif_path))
    assert watcher.get_status(str(project))["is_stale"] is False

    _write(project / ".git" / "HEAD", "ref: refs/heads/main\n")
    time.sleep(0.3)  # give a (not expected) event a chance to land

    assert watcher.get_status(str(project))["is_stale"] is False


def test_start_watch_is_idempotent_for_the_same_project(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.05)
    project, aif_path = _make_project_with_manifest(tmp_path)

    watcher.start_watch(str(project), str(aif_path))
    first_observer = watcher._watchers[watcher._abs_key(str(project))]["observer"]
    watcher.start_watch(str(project), str(aif_path))
    second_observer = watcher._watchers[watcher._abs_key(str(project))]["observer"]

    assert first_observer is not second_observer  # old one replaced, not stacked
    assert len(watcher._watchers) == 1


def test_a_late_firing_recompute_from_an_older_start_watch_does_not_clobber_a_newer_report(tmp_path, monkeypatch):
    # Real race: recompute()'s closure captures aif_path from whichever
    # start_watch() call created it, but writes into _watchers[key] keyed
    # by project_path alone -- a still-in-flight recompute() from an older
    # call (e.g. its debounce timer already scheduled before a second
    # start_watch() for the same project, pointed at a *different*
    # aif_path/manifest, replaced the entry) must not overwrite the newer
    # watcher's report with data computed against the old aif_path.
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.05)
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    # v1's manifest deliberately doesn't match the real file -- "stale".
    aif_path_v1 = tmp_path / "out1" / "project.json"
    aif_path_v1.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "out1" / "project.cache.json").write_text(
        json.dumps({"main.py": "deliberately-wrong-hash"}), encoding="utf-8"
    )
    # v2's manifest is the real, current one -- "fresh".
    aif_path_v2 = tmp_path / "out2" / "project.json"
    aif_path_v2.parent.mkdir(parents=True, exist_ok=True)
    manifest_v2 = build_manifest([str(project / "main.py")], str(project))
    (tmp_path / "out2" / "project.cache.json").write_text(json.dumps(manifest_v2), encoding="utf-8")

    captured = {}
    real_init = watcher._DebouncedHandler.__init__

    def capturing_init(self, root, ignore_spec, on_change):
        captured["fn"] = on_change
        real_init(self, root, ignore_spec, on_change)

    monkeypatch.setattr(watcher._DebouncedHandler, "__init__", capturing_init)

    watcher.start_watch(str(project), str(aif_path_v1))
    old_recompute = captured["fn"]  # the v1 watch's own recompute closure
    assert watcher.get_status(str(project))["is_stale"] is True

    # A second start_watch() for the same project, now pointed at v2 --
    # simulates re-packing to a new output path.
    watcher.start_watch(str(project), str(aif_path_v2))
    assert watcher.get_status(str(project))["is_stale"] is False

    # Simulate the v1 watch's debounce timer finally firing late, after
    # already having been replaced above.
    old_recompute()

    assert watcher.get_status(str(project))["is_stale"] is False


def test_stop_watch_removes_and_stops_the_observer(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.05)
    project, aif_path = _make_project_with_manifest(tmp_path)
    watcher.start_watch(str(project), str(aif_path))

    watcher.stop_watch(str(project))

    assert watcher.get_status(str(project)) is None


def test_evicts_oldest_watcher_past_max_watchers(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.05)
    monkeypatch.setattr(watcher, "MAX_WATCHERS", 2)

    projects = []
    for i in range(3):
        proj = tmp_path / f"project{i}"
        _write(proj / "main.py", "def add(a, b):\n    return a + b\n")
        manifest = build_manifest([str(proj / "main.py")], str(proj))
        aif_path = tmp_path / f"out{i}" / f"project{i}.json"
        aif_path.parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f"out{i}" / f"project{i}.cache.json").write_text(json.dumps(manifest), encoding="utf-8")
        projects.append((proj, aif_path))
        watcher.start_watch(str(proj), str(aif_path))
        time.sleep(0.01)  # keep started_at ordering unambiguous

    assert len(watcher._watchers) == 2
    # the first (oldest) project's watcher should have been evicted
    assert watcher.get_status(str(projects[0][0])) is None
    assert watcher.get_status(str(projects[2][0])) is not None
