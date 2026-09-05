"""Covers checkpoint.py's non-interactive failure handling -- not the full
pack() pipeline, which needs a live LLM call. handle_llm_failure() and
resume_checkpoint_choice() are the two places pack() used to call input()
unconditionally, crashing with EOFError under closed stdin (e.g. `pack
--auto-correct` in CI) instead of degrading gracefully.
"""

import builtins
from pathlib import Path

from ziplex import checkpoint


def test_handle_llm_failure_non_interactive_checkpoints_without_prompting(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)

    def _unexpected_input(*a, **k):
        raise AssertionError("input() must not be called when interactive=False")

    monkeypatch.setattr(builtins, "input", _unexpected_input)

    result = checkpoint.handle_llm_failure(
        "rules", "코딩 룰", {"project": {"name": "x"}}, "some/project", interactive=False
    )

    assert result == "EXIT"
    assert checkpoint.load_checkpoint("some/project") == {"project": {"name": "x"}}


def test_handle_llm_failure_interactive_still_prompts(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "1")

    result = checkpoint.handle_llm_failure(
        "rules", "코딩 룰", {"project": {"name": "x"}}, "some/project", interactive=True
    )

    assert result is None  # "1" = retry


def test_resume_checkpoint_choice_non_interactive_always_resumes(monkeypatch):
    def _unexpected_input(*a, **k):
        raise AssertionError("input() must not be called when interactive=False")

    monkeypatch.setattr(builtins, "input", _unexpected_input)

    assert checkpoint.resume_checkpoint_choice(interactive=False) is True


def test_resume_checkpoint_choice_interactive_respects_choice(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "2")
    assert checkpoint.resume_checkpoint_choice(interactive=True) is False

    monkeypatch.setattr(builtins, "input", lambda *a, **k: "")
    assert checkpoint.resume_checkpoint_choice(interactive=True) is True


def test_build_snapshot_keys_files_data_by_relative_name(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    fp = str(root / "src" / "main.py")

    snapshot = checkpoint.build_snapshot(
        root, {fp: {"summary": "does a thing"}}, rules=["rule one"], prompt="a guide"
    )

    assert snapshot == {
        "project": {"name": "project", "prompt": "a guide", "language": "en"},
        "rules": ["rule one"],
        "files_data": {"src/main.py": {"summary": "does a thing"}},
    }


def test_build_snapshot_defaults_rules_to_none_and_prompt_to_empty(tmp_path):
    # rules=None (not coalesced into []) is the "not computed yet" signal
    # unpack_snapshot()'s own rules_computed reads back -- see that
    # function's docstring for why this distinction from a genuinely
    # computed, empty rules=[] answer matters.
    root = tmp_path / "project"
    root.mkdir()
    snapshot = checkpoint.build_snapshot(root, {})
    assert snapshot == {
        "project": {"name": "project", "prompt": "", "language": "en"},
        "rules": None,
        "files_data": {},
    }


def test_build_snapshot_records_the_given_lang(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    snapshot = checkpoint.build_snapshot(root, {}, lang="ko")
    assert snapshot["project"]["language"] == "ko"


def test_unpack_snapshot_round_trips_build_snapshot(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    fp = str(root / "src" / "main.py")

    snapshot = checkpoint.build_snapshot(
        root, {fp: {"summary": "does a thing"}}, rules=["rule one"], prompt="a guide", lang="ko"
    )
    rules, prompt, files_data, lang, rules_computed = checkpoint.unpack_snapshot(snapshot)

    assert rules == ["rule one"]
    assert prompt == "a guide"
    assert files_data == {"src/main.py": {"summary": "does a thing"}}
    assert lang == "ko"
    assert rules_computed is True


def test_unpack_snapshot_returns_empty_defaults_for_none():
    assert checkpoint.unpack_snapshot(None) == ([], "", {}, "en", False)


def test_unpack_snapshot_defaults_lang_to_english_for_a_checkpoint_predating_the_field(tmp_path):
    # A checkpoint saved before `language` existed has no such key at all --
    # must default to "en" (the only language that existed then), same
    # convention project.language's own missing-field default uses.
    root = tmp_path / "project"
    root.mkdir()
    snapshot = checkpoint.build_snapshot(root, {}, rules=["r"], prompt="p")
    del snapshot["project"]["language"]

    _, _, _, lang, _ = checkpoint.unpack_snapshot(snapshot)
    assert lang == "en"


def test_unpack_snapshot_reports_rules_computed_false_when_never_set(tmp_path):
    # build_snapshot() with no rules param at all (mid-extraction, or before
    # it ever ran) -- unpack_snapshot() must be able to tell this apart from
    # a checkpoint that legitimately restored a real, empty rules answer
    # (the next test below), or packager.py's own resume logic would
    # needlessly re-run analyze_rules() for the latter.
    root = tmp_path / "project"
    root.mkdir()
    snapshot = checkpoint.build_snapshot(root, {})

    rules, _, _, _, rules_computed = checkpoint.unpack_snapshot(snapshot)
    assert rules == []
    assert rules_computed is False


def test_unpack_snapshot_reports_rules_computed_true_for_a_genuine_empty_answer(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    snapshot = checkpoint.build_snapshot(root, {}, rules=[])

    rules, _, _, _, rules_computed = checkpoint.unpack_snapshot(snapshot)
    assert rules == []
    assert rules_computed is True


def test_unpack_snapshot_treats_a_pre_fix_checkpoints_rules_as_computed(tmp_path):
    # An older checkpoint saved before this fix always has a real list under
    # "rules" (the old `rules or []` coalescing, never None) -- there's no
    # way to retroactively know whether that list was a genuine answer or
    # simply never computed, so it reads as computed either way: the same
    # "trust it, don't re-extract" behavior such a checkpoint already got
    # before this fix existed, not a regression for it.
    root = tmp_path / "project"
    root.mkdir()
    snapshot = checkpoint.build_snapshot(root, {}, rules=["r"])
    snapshot["rules"] = []  # simulate the pre-fix "rules or []" coalescing

    _, _, _, _, rules_computed = checkpoint.unpack_snapshot(snapshot)
    assert rules_computed is True


def test_save_load_delete_checkpoint_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)

    assert checkpoint.load_checkpoint("some/project") is None

    checkpoint.save_checkpoint("some/project", {"rules": ["a rule"]})
    assert checkpoint.load_checkpoint("some/project") == {"rules": ["a rule"]}

    checkpoint.delete_checkpoint("some/project")
    assert checkpoint.load_checkpoint("some/project") is None


def test_checkpoints_for_same_named_projects_at_different_paths_dont_collide(tmp_path, monkeypatch):
    # Two different projects that happen to share a folder basename (e.g.
    # C:\clients\acme\backend and C:\clients\other\backend) must not load
    # or overwrite each other's checkpoint -- a bare basename-only filename
    # would make the second project's pack silently auto-resume (or clobber)
    # the first's leftover checkpoint.
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
    project_a = tmp_path / "acme" / "backend"
    project_b = tmp_path / "other" / "backend"
    project_a.mkdir(parents=True)
    project_b.mkdir(parents=True)

    checkpoint.save_checkpoint(str(project_a), {"rules": ["a's rule"]})
    checkpoint.save_checkpoint(str(project_b), {"rules": ["b's rule"]})

    assert checkpoint.load_checkpoint(str(project_a)) == {"rules": ["a's rule"]}
    assert checkpoint.load_checkpoint(str(project_b)) == {"rules": ["b's rule"]}

    checkpoint.delete_checkpoint(str(project_a))
    assert checkpoint.load_checkpoint(str(project_a)) is None
    assert checkpoint.load_checkpoint(str(project_b)) == {"rules": ["b's rule"]}  # untouched


def test_delete_checkpoint_is_a_no_op_when_none_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
    checkpoint.delete_checkpoint("some/project")  # must not raise


def test_list_checkpoints_is_empty_when_checkpoint_dir_is_absent(tmp_path, monkeypatch):
    # CHECKPOINT_DIR itself doesn't exist yet (no pack has ever failed) --
    # must not raise just because .glob() is called on a missing directory.
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "never-created")
    assert checkpoint.list_checkpoints() == []


def test_list_checkpoints_reports_project_name_and_pending_file_count(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
    project_a = tmp_path / "proj-a"
    project_a.mkdir()
    checkpoint.save_checkpoint(
        str(project_a), {"project": {"name": "proj-a"}, "files_data": {"a.py": {}, "b.py": {}}}
    )

    [entry] = checkpoint.list_checkpoints()

    assert entry["project_name"] == "proj-a"
    assert entry["pending_files"] == 2
    assert entry["path"].exists()
    assert entry["size_bytes"] > 0


def test_list_checkpoints_survives_a_corrupted_file(tmp_path, monkeypatch):
    # A checkpoint file that fails to parse (killed mid-write, hand-edited)
    # is still listed -- `ziplex checkpoint list` must surface it so
    # `ziplex checkpoint clean --all` has something to remove, rather than
    # silently dropping or raising on it.
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
    (tmp_path / "broken-abcd1234.json").write_text("{not valid json", encoding="utf-8")

    [entry] = checkpoint.list_checkpoints()

    assert entry["project_name"] == "(읽기 실패)"
    assert entry["pending_files"] == 0


def test_list_checkpoints_sorted_by_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
    checkpoint.save_checkpoint(str(tmp_path / "zzz"), {"project": {"name": "zzz"}})
    checkpoint.save_checkpoint(str(tmp_path / "aaa"), {"project": {"name": "aaa"}})

    names = [entry["project_name"] for entry in checkpoint.list_checkpoints()]

    assert names == sorted(names)  # "zzz-<hash>.json" sorts after "aaa-<hash>.json"


def test_clear_all_checkpoints_removes_every_file_and_returns_the_count(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
    checkpoint.save_checkpoint(str(tmp_path / "proj1"), {"project": {"name": "proj1"}})
    checkpoint.save_checkpoint(str(tmp_path / "proj2"), {"project": {"name": "proj2"}})

    removed = checkpoint.clear_all_checkpoints()

    assert removed == 2
    assert checkpoint.list_checkpoints() == []


def test_clear_all_checkpoints_is_a_no_op_when_checkpoint_dir_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path / "never-created")
    assert checkpoint.clear_all_checkpoints() == 0


def test_clear_all_checkpoints_survives_a_file_deleted_between_glob_and_unlink(tmp_path, monkeypatch):
    # Real race: a separate pack() run finishing successfully and deleting
    # its own checkpoint (delete_checkpoint()) between this loop's glob()
    # and the moment it reaches that same file's unlink() call -- must skip
    # it and keep removing the rest, not crash with an uncaught
    # FileNotFoundError partway through.
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", tmp_path)
    checkpoint.save_checkpoint(str(tmp_path / "proj1"), {"project": {"name": "proj1"}})
    checkpoint.save_checkpoint(str(tmp_path / "proj2"), {"project": {"name": "proj2"}})

    real_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self.name.startswith("proj1-"):
            # Simulate a concurrent delete_checkpoint() winning the race:
            # the file is actually gone by the time *this* unlink() call
            # would run, which is exactly what raises FileNotFoundError for
            # real.
            real_unlink(self, *args, **kwargs)
            raise FileNotFoundError("simulated concurrent delete")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    removed = checkpoint.clear_all_checkpoints()

    assert removed == 1  # only proj2's file counted -- proj1's raised, but is still gone
    assert checkpoint.list_checkpoints() == []
