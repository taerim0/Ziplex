"""file/selector.py's review_dangerous_files() -- the terminal prompt a
human sees mid-pack() when scan_files() flagged something (see
packager.pack()'s own comment on why this is gated by `interactive`, not
`auto`). select_files() itself is exercised indirectly through
test_pack_integration.py's end-to-end runs; this file is scoped to the new
review step specifically.
"""

import builtins

from ziplex.file.selector import review_dangerous_files


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
