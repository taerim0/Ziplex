"""corrector.py's interactive terminal review flow -- previously untested
(4% coverage: only what other modules happened to exercise by importing it).
`--auto-correct` skips this module entirely, so it's the one path a plain
`ziplex pack` run without that flag actually walks through.

Every input() sequence below is fed as a fixed list via a small helper
(`_queued_input`) so a call the code doesn't make (or an extra one it does)
fails loudly with StopIteration/leftover values, instead of silently reusing
a stale answer the way a single `lambda: "..."` would.
"""

import builtins

import pytest

from ziplex.corrector import correct_aif, correct_relationships
from ziplex.file.relationship import build_tree


def _queued_input(monkeypatch, values):
    """Feeds `values` to input() one at a time, regardless of the prompt
    text passed (corrector.py mixes input() and input("...")). Asserts
    every value was actually consumed -- a leftover value means the code
    under test made fewer input() calls than expected.
    """
    it = iter(values)
    consumed = []

    def _fake_input(*args, **kwargs):
        try:
            value = next(it)
        except StopIteration:
            pytest.fail("input() called more times than values were queued")
        consumed.append(value)
        return value

    monkeypatch.setattr(builtins, "input", _fake_input)

    def _assert_all_consumed():
        remaining = list(it)
        assert remaining == [], f"queued input values never consumed: {remaining}"

    return _assert_all_consumed


def _file(summary="Does a thing.", confidence=1.0, signatures=None, dependencies=None):
    return {
        "summary": summary,
        "confidence": confidence,
        "signatures": signatures or [],
        "dependencies": dependencies or [],
    }


def _make_aif(folders=None):
    aif = {
        "project": {"name": "MyProj", "prompt": "Original AI guide"},
        "rules": ["Use snake_case"],
        "files": {
            "a.py": _file("Does A things.", confidence=1.0, signatures=["do_a()"]),
            "b.py": _file("Does B things.", confidence=0.1, signatures=["do_b()", "helper()"]),
        },
    }
    if folders is not None:
        aif["folders"] = folders
    return aif


# --- correct_relationships ---------------------------------------------


def test_correct_relationships_quits_immediately_on_blank_input(monkeypatch, capsys):
    assert_consumed = _queued_input(monkeypatch, [""])
    aif = _make_aif()
    original_files = {name: dict(data) for name, data in aif["files"].items()}

    result = correct_relationships(aif)

    assert result["files"] == original_files
    assert_consumed()


def test_correct_relationships_quits_on_q(monkeypatch):
    assert_consumed = _queued_input(monkeypatch, ["q"])
    aif = _make_aif()

    result = correct_relationships(aif)

    assert result["files"]["a.py"]["dependencies"] == []
    assert_consumed()


def test_correct_relationships_moves_a_file_under_a_new_parent(monkeypatch):
    # files = ["a.py", "b.py"] -- move b.py (index 2) under a.py (index 1).
    assert_consumed = _queued_input(monkeypatch, ["2", "1", "q"])
    aif = _make_aif()

    result = correct_relationships(aif)

    assert result["files"]["a.py"]["dependencies"] == ["b.py"]
    assert_consumed()


def test_correct_relationships_reports_out_of_range_move_index(monkeypatch, capsys):
    assert_consumed = _queued_input(monkeypatch, ["99", "q"])
    aif = _make_aif()

    correct_relationships(aif)

    assert "범위 초과" in capsys.readouterr().out
    assert_consumed()


def test_correct_relationships_reports_invalid_move_index(monkeypatch, capsys):
    assert_consumed = _queued_input(monkeypatch, ["not-a-number", "q"])
    aif = _make_aif()

    correct_relationships(aif)

    assert "잘못된 입력" in capsys.readouterr().out
    assert_consumed()


def test_correct_relationships_cancelling_parent_choice_leaves_the_loop_open(monkeypatch):
    # Pick a.py to move, then cancel the parent choice with "q" -- the outer
    # loop must still be alive afterward, so a second "q" is needed to exit.
    assert_consumed = _queued_input(monkeypatch, ["1", "q", "q"])
    aif = _make_aif()

    result = correct_relationships(aif)

    assert result["files"]["a.py"]["dependencies"] == []
    assert_consumed()


def test_correct_relationships_reports_out_of_range_parent_index(monkeypatch, capsys):
    assert_consumed = _queued_input(monkeypatch, ["1", "99", "q"])
    aif = _make_aif()

    correct_relationships(aif)

    assert "범위 초과" in capsys.readouterr().out
    assert_consumed()


def test_correct_relationships_rejects_a_file_as_its_own_parent(monkeypatch, capsys):
    assert_consumed = _queued_input(monkeypatch, ["1", "1", "q"])
    aif = _make_aif()

    correct_relationships(aif)

    assert "같은 파일 선택 불가" in capsys.readouterr().out
    assert_consumed()


def test_correct_relationships_tree_view_shows_external_deps_and_existing_cycles(monkeypatch, capsys):
    # print_current_tree()'s own two branches, distinct from move_file()'s
    # CycleError guard above: an unresolved (external, e.g. a third-party
    # package) dependency prints with a different icon, and a cycle that
    # already exists in the data (not one this loop is about to create)
    # prints "생략" and stops recursing instead of looping forever.
    # c.py -> a.py -> b.py -> a.py: b.py's edge back to a.py (already its
    # own ancestor on this path) is the pre-existing cycle.
    aif = {
        "files": {
            "c.py": {"dependencies": ["a.py", "requests"]},
            "a.py": {"dependencies": ["b.py"]},
            "b.py": {"dependencies": ["a.py"]},
        }
    }
    assert_consumed = _queued_input(monkeypatch, ["q"])

    correct_relationships(aif)

    out = capsys.readouterr().out
    assert "📦 requests" in out
    assert "순환 참조 → 생략" in out
    assert_consumed()


def test_correct_relationships_reports_a_cycle_instead_of_crashing(monkeypatch, capsys):
    # a.py already depends on b.py; moving a.py under b.py would close a
    # cycle (b -> a -> b) -- move_file() must raise CycleError, and the loop
    # must catch it and keep going rather than propagating.
    aif = _make_aif()
    aif["files"]["a.py"]["dependencies"] = ["b.py"]
    assert_consumed = _queued_input(monkeypatch, ["1", "2", "q"])

    result = correct_relationships(aif)

    assert "순환 참조" in capsys.readouterr().out
    # the attempted move must not have gone through
    assert result["files"]["a.py"]["dependencies"] == ["b.py"]
    assert result["files"]["b.py"]["dependencies"] == []
    assert_consumed()


# --- correct_aif ---------------------------------------------------------


def test_correct_aif_keeps_everything_on_blank_answers(monkeypatch):
    # name, prompt, rule_input, b.py's summary (needs review, confidence
    # 0.1), then correct_relationships' own move-index prompt.
    assert_consumed = _queued_input(monkeypatch, ["", "", "", "", ""])
    aif = _make_aif()

    result = correct_aif(aif)

    assert result["project"]["name"] == "MyProj"
    assert result["project"]["prompt"] == "Original AI guide"
    assert result["rules"] == ["Use snake_case"]
    assert result["files"]["a.py"]["summary"] == "Does A things."
    assert result["files"]["b.py"]["summary"] == "Does B things."
    assert_consumed()


def test_correct_aif_applies_every_edit_and_finalizes(monkeypatch):
    folders = {
        "src": {"summary": "old folder summary", "confidence": 0.1, "file_count": 2},
        ".": {"summary": "root summary", "confidence": 1.0, "file_count": 1},
    }
    aif = _make_aif(folders=folders)

    values = [
        "New Name",           # project name
        "New AI guide",       # project prompt
        "a",                  # rule_input: add a rule
        "A new rule",         # the rule text itself
        "New B summary",      # b.py needs review (confidence 0.1)
        "New src summary",    # "src" folder needs review (confidence 0.1)
        "2",                  # correct_relationships: move b.py (index 2)...
        "1",                  # ...under a.py (index 1)
        "q",                  # quit the move loop
    ]
    assert_consumed = _queued_input(monkeypatch, values)

    result = correct_aif(aif)

    assert result["project"]["name"] == "New Name"
    assert result["project"]["prompt"] == "New AI guide"
    assert result["rules"] == ["Use snake_case", "A new rule"]
    assert result["files"]["b.py"]["summary"] == "New B summary"
    assert result["folders"]["src"]["summary"] == "New src summary"
    # "." never dropped below REVIEW_THRESHOLD, so it must have been
    # auto-kept, not prompted -- its summary is untouched.
    assert result["folders"]["."]["summary"] == "root summary"

    # finalize_aif() ran: relationships built from the post-move graph,
    # and the now-redundant working fields are gone.
    assert result["relationships"] == build_tree(
        {"a.py": {"dependencies": ["b.py"]}, "b.py": {"dependencies": []}}
    )
    for data in result["files"].values():
        assert "signatures" not in data
        assert "dependencies" not in data

    assert_consumed()


def test_correct_aif_deletes_a_rule_by_index(monkeypatch):
    values = [
        "",       # name
        "",       # prompt
        "d1",     # delete rule #1
        "",       # b.py needs review -> keep
        "",       # correct_relationships: quit immediately
    ]
    assert_consumed = _queued_input(monkeypatch, values)
    aif = _make_aif()
    aif["rules"] = ["Rule one", "Rule two"]

    result = correct_aif(aif)

    assert result["rules"] == ["Rule two"]
    assert_consumed()


def test_correct_aif_silently_ignores_an_out_of_range_rule_deletion(monkeypatch):
    # A syntactically valid but out-of-range "d99" is guarded by the same
    # bounds check as the valid-index branch, so it's a silent no-op --
    # unlike a non-numeric "d" suffix (see the next test), it never reaches
    # the (ValueError, IndexError) handler at all.
    values = ["", "", "d99", "", ""]
    assert_consumed = _queued_input(monkeypatch, values)
    aif = _make_aif()
    aif["rules"] = ["Only rule"]

    result = correct_aif(aif)

    assert result["rules"] == ["Only rule"]
    assert_consumed()


def test_correct_aif_reports_a_non_numeric_rule_deletion(monkeypatch, capsys):
    values = ["", "", "dxyz", "", ""]
    assert_consumed = _queued_input(monkeypatch, values)
    aif = _make_aif()
    aif["rules"] = ["Only rule"]

    result = correct_aif(aif)

    assert result["rules"] == ["Only rule"]
    assert "잘못된 입력" in capsys.readouterr().out
    assert_consumed()


def test_correct_aif_hints_at_hidden_signatures_past_ten(monkeypatch, capsys):
    aif = _make_aif()
    aif["files"]["b.py"] = _file(
        "Does B things.",
        confidence=0.1,
        signatures=[f"fn{i}()" for i in range(12)],
    )
    values = ["", "", "", "", ""]  # name, prompt, rule_input, b.py summary, relationships-quit
    assert_consumed = _queued_input(monkeypatch, values)

    correct_aif(aif)

    assert "... 외 2개" in capsys.readouterr().out
    assert_consumed()


def test_correct_aif_never_prompts_for_a_file_above_the_review_threshold(monkeypatch):
    # Every file confident enough to be auto-kept -- the per-file review
    # loop must consume zero input() calls.
    aif = _make_aif()
    aif["files"]["b.py"]["confidence"] = 1.0
    values = ["", "", "", ""]  # name, prompt, rule_input, relationships-quit
    assert_consumed = _queued_input(monkeypatch, values)

    result = correct_aif(aif)

    assert result["files"]["b.py"]["summary"] == "Does B things."
    assert_consumed()
