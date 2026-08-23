import json
from pathlib import Path

from freshness import hash_file, build_manifest, check_freshness, load_previous_summaries


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_hash_file_is_stable_for_identical_content(tmp_path):
    _write(tmp_path / "a.py", "x = 1\n")
    _write(tmp_path / "b.py", "x = 1\n")
    assert hash_file(str(tmp_path / "a.py")) == hash_file(str(tmp_path / "b.py"))


def test_hash_file_differs_for_different_content(tmp_path):
    _write(tmp_path / "a.py", "x = 1\n")
    _write(tmp_path / "b.py", "x = 2\n")
    assert hash_file(str(tmp_path / "a.py")) != hash_file(str(tmp_path / "b.py"))


def test_hash_file_returns_none_for_binary(tmp_path):
    (tmp_path / "sprite.bin").write_bytes(bytes(range(256)))
    assert hash_file(str(tmp_path / "sprite.bin")) is None


def test_build_manifest_keys_by_relative_path(tmp_path):
    _write(tmp_path / "sub" / "a.py", "x = 1\n")
    manifest = build_manifest([str(tmp_path / "sub" / "a.py")], str(tmp_path))
    assert list(manifest.keys()) == ["sub/a.py"]


def test_check_freshness_reports_no_drift_when_nothing_changed(tmp_path):
    _write(tmp_path / "a.py", "x = 1\n")
    file_path = str(tmp_path / "a.py")
    manifest = build_manifest([file_path], str(tmp_path))

    report = check_freshness([file_path], str(tmp_path), manifest)

    assert report.is_stale is False
    assert report.changed == []
    assert report.added == []
    assert report.removed == []
    assert report.unchanged == ["a.py"]


def test_check_freshness_detects_a_changed_file(tmp_path):
    _write(tmp_path / "a.py", "x = 1\n")
    file_path = str(tmp_path / "a.py")
    manifest = build_manifest([file_path], str(tmp_path))

    _write(tmp_path / "a.py", "x = 2\n")  # edit after the manifest was taken

    report = check_freshness([file_path], str(tmp_path), manifest)

    assert report.is_stale is True
    assert report.changed == ["a.py"]
    assert report.added == []
    assert report.removed == []
    assert report.unchanged == []


def test_check_freshness_detects_added_and_removed_files(tmp_path):
    _write(tmp_path / "a.py", "x = 1\n")
    _write(tmp_path / "b.py", "y = 1\n")
    old_manifest = build_manifest([str(tmp_path / "a.py"), str(tmp_path / "b.py")], str(tmp_path))

    # b.py deleted, c.py added -- only a.py survives unchanged
    (tmp_path / "b.py").unlink()
    _write(tmp_path / "c.py", "z = 1\n")

    current_files = [str(tmp_path / "a.py"), str(tmp_path / "c.py")]
    report = check_freshness(current_files, str(tmp_path), old_manifest)

    assert report.is_stale is True
    assert report.changed == []
    assert report.added == ["c.py"]
    assert report.removed == ["b.py"]
    assert report.unchanged == ["a.py"]


def test_load_previous_summaries_returns_empty_when_no_previous_pack_exists(tmp_path):
    assert load_previous_summaries("some/project", ["a.py"], tmp_path) == {}


def test_load_previous_summaries_reuses_summary_for_an_unchanged_file(tmp_path):
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    project = tmp_path / "project"
    _write(project / "a.py", "x = 1\n")

    (result_dir / "project.json").write_text(
        json.dumps({"files": {"a.py": {"summary": "does a thing"}}}), encoding="utf-8"
    )
    (result_dir / "project.cache.json").write_text(
        json.dumps(build_manifest([str(project / "a.py")], str(project))), encoding="utf-8"
    )

    reused = load_previous_summaries(str(project), [str(project / "a.py")], result_dir)
    assert reused == {"a.py": "does a thing"}


def test_load_previous_summaries_excludes_a_changed_file(tmp_path):
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    project = tmp_path / "project"
    _write(project / "a.py", "x = 1\n")

    (result_dir / "project.json").write_text(
        json.dumps({"files": {"a.py": {"summary": "does a thing"}}}), encoding="utf-8"
    )
    (result_dir / "project.cache.json").write_text(
        json.dumps(build_manifest([str(project / "a.py")], str(project))), encoding="utf-8"
    )

    _write(project / "a.py", "x = 2\n")  # edit after the manifest was taken

    assert load_previous_summaries(str(project), [str(project / "a.py")], result_dir) == {}


def test_load_previous_summaries_rejects_a_cross_project_basename_collision(tmp_path):
    # Two different projects that happen to share a result-directory
    # basename ("backend") must not have one's cached summaries silently
    # applied to the other, even if a handful of files coincidentally hash-
    # match (a shared boilerplate .gitignore, an empty __init__.py) -- the
    # bulk of the *current* project's files are unrecognized by the
    # previous manifest, which is the signal this isn't a real re-pack.
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    old_project = tmp_path / "acme" / "backend"
    _write(old_project / "shared.txt", "same content\n")
    for i in range(5):
        _write(old_project / f"old_{i}.py", f"old file {i}\n")

    (result_dir / "backend.json").write_text(
        json.dumps({"files": {"shared.txt": {"summary": "old project's summary"}}}), encoding="utf-8"
    )
    old_files = [str(old_project / "shared.txt")] + [str(old_project / f"old_{i}.py") for i in range(5)]
    (result_dir / "backend.cache.json").write_text(
        json.dumps(build_manifest(old_files, str(old_project))), encoding="utf-8"
    )

    new_project = tmp_path / "other" / "backend"
    _write(new_project / "shared.txt", "same content\n")  # coincidental hash match
    for i in range(5):
        _write(new_project / f"new_{i}.py", f"new file {i}\n")
    new_files = [str(new_project / "shared.txt")] + [str(new_project / f"new_{i}.py") for i in range(5)]

    assert load_previous_summaries(str(new_project), new_files, result_dir) == {}


def test_load_previous_summaries_tolerates_a_corrupt_cache_file(tmp_path):
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    (result_dir / "project.json").write_text('{"files": {}}', encoding="utf-8")
    (result_dir / "project.cache.json").write_text("{ not valid json", encoding="utf-8")

    assert load_previous_summaries("some/project", ["a.py"], result_dir) == {}
