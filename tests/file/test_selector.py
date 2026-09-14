"""file/selector.py's two interactive terminal prompts: review_dangerous_files()
(a human decides whether to include a file scan_files() flagged as
sensitive -- see packager.pack()'s own comment on why this is gated by
`interactive`, not `auto`) and select_files() (the main "which of the safe
files actually go into the pack" picker every non-`--auto` run walks
through). display_files() is select_files()'s own listing helper, covered
alongside it.
"""

import builtins

from ziplex.file.selector import display_files, review_dangerous_files, select_files


def _entry(file, reason="패턴 일치", line=3, matched_text='API_KEY = "x"'):
    return {"file": file, "reason": reason, "line": line, "matched_text": matched_text}


def test_review_dangerous_files_excludes_everything_on_blank_input(monkeypatch, tmp_path):
    monkeypatch.setattr(builtins, "input", lambda: "")
    dangerous = [_entry(str(tmp_path / "secret.env"))]

    assert review_dangerous_files(dangerous, str(tmp_path)) == []


def test_review_dangerous_files_includes_the_chosen_numbers(monkeypatch, tmp_path):
    monkeypatch.setattr(builtins, "input", lambda: "1, 3")
    dangerous = [
        _entry(str(tmp_path / "a.env")),
        _entry(str(tmp_path / "b.env")),
        _entry(str(tmp_path / "c.env")),
    ]

    included = review_dangerous_files(dangerous, str(tmp_path))
    assert included == [str(tmp_path / "a.env"), str(tmp_path / "c.env")]


def test_review_dangerous_files_ignores_an_out_of_range_number(monkeypatch, tmp_path):
    monkeypatch.setattr(builtins, "input", lambda: "1, 99")
    dangerous = [_entry(str(tmp_path / "a.env"))]

    assert review_dangerous_files(dangerous, str(tmp_path)) == [str(tmp_path / "a.env")]


def test_review_dangerous_files_excludes_everything_on_invalid_input(monkeypatch, tmp_path):
    monkeypatch.setattr(builtins, "input", lambda: "not a number")
    dangerous = [_entry(str(tmp_path / "a.env"))]

    assert review_dangerous_files(dangerous, str(tmp_path)) == []


def test_display_files_lists_each_file_relative_to_root(tmp_path, capsys):
    files = [str(tmp_path / "a.py"), str(tmp_path / "sub" / "b.py")]

    display_files(files, str(tmp_path))

    out = capsys.readouterr().out
    assert "2개" in out
    assert "[1] a.py" in out
    assert "[2] sub" in out and "b.py" in out


def test_select_files_cancels_on_q(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(builtins, "input", lambda: "q")
    files = [str(tmp_path / "a.py"), str(tmp_path / "b.py")]

    result = select_files(files, str(tmp_path))

    assert result == []
    assert "취소됨" in capsys.readouterr().out


def test_select_files_is_case_insensitive_for_cancel_and_all(monkeypatch, tmp_path):
    files = [str(tmp_path / "a.py"), str(tmp_path / "b.py")]

    monkeypatch.setattr(builtins, "input", lambda: "Q")
    assert select_files(files, str(tmp_path)) == []

    monkeypatch.setattr(builtins, "input", lambda: "A")
    assert select_files(files, str(tmp_path)) == files


def test_select_files_selects_all_on_a(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(builtins, "input", lambda: "a")
    files = [str(tmp_path / "a.py"), str(tmp_path / "b.py")]

    result = select_files(files, str(tmp_path))

    assert result == files
    assert "2개 선택됨" in capsys.readouterr().out


def test_select_files_selects_the_chosen_numbers(monkeypatch, tmp_path):
    monkeypatch.setattr(builtins, "input", lambda: "1, 3")
    files = [str(tmp_path / f"{n}.py") for n in ("a", "b", "c")]

    result = select_files(files, str(tmp_path))

    assert result == [files[0], files[2]]


def test_select_files_ignores_an_out_of_range_number(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(builtins, "input", lambda: "1, 99")
    files = [str(tmp_path / "a.py")]

    result = select_files(files, str(tmp_path))

    assert result == [files[0]]
    assert "범위 초과" in capsys.readouterr().out


def test_select_files_returns_empty_on_invalid_input(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(builtins, "input", lambda: "not a number")
    files = [str(tmp_path / "a.py")]

    result = select_files(files, str(tmp_path))

    assert result == []
    assert "잘못된 입력" in capsys.readouterr().out
