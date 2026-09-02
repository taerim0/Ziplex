from pathlib import Path

import pytest

from ziplex.search import search_files, read_detail_range


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_search_finds_matching_lines_across_files(tmp_path):
    _write(tmp_path / "a.py", "def get_user():\n    pass\n")
    _write(tmp_path / "sub" / "b.py", "def get_token():\n    pass\n")

    matches = search_files(
        [str(tmp_path / "a.py"), str(tmp_path / "sub" / "b.py")],
        str(tmp_path),
        r"def get_\w+",
    )

    assert [m.file for m in matches] == ["a.py", "sub/b.py"]
    assert matches[0].line_number == 1
    assert matches[0].line == "def get_user():"


def test_search_is_case_sensitive_unless_ignore_case(tmp_path):
    _write(tmp_path / "a.py", "TODO: fix this\n")

    assert search_files([str(tmp_path / "a.py")], str(tmp_path), "todo") == []
    assert len(search_files([str(tmp_path / "a.py")], str(tmp_path), "todo", ignore_case=True)) == 1


def test_search_returns_context_lines(tmp_path):
    _write(tmp_path / "a.py", "one\ntwo\nMATCH\nfour\nfive\n")

    [match] = search_files([str(tmp_path / "a.py")], str(tmp_path), "MATCH", context_lines=1)

    assert match.context_before == ["two"]
    assert match.line == "MATCH"
    assert match.context_after == ["four"]


def test_search_skips_binary_files(tmp_path):
    (tmp_path / "sprite.bin").write_bytes(bytes(range(256)))
    _write(tmp_path / "a.py", "MATCH\n")

    matches = search_files(
        [str(tmp_path / "sprite.bin"), str(tmp_path / "a.py")], str(tmp_path), "MATCH"
    )
    assert [m.file for m in matches] == ["a.py"]


def test_search_max_results_caps_matches_across_files(tmp_path):
    _write(tmp_path / "a.py", "MATCH\nMATCH\nMATCH\n")
    _write(tmp_path / "b.py", "MATCH\nMATCH\nMATCH\n")

    matches = search_files(
        [str(tmp_path / "a.py"), str(tmp_path / "b.py")], str(tmp_path), "MATCH", max_results=4
    )

    assert len(matches) == 4


def test_search_max_results_none_is_unlimited(tmp_path):
    _write(tmp_path / "a.py", "MATCH\n" * 10)

    matches = search_files([str(tmp_path / "a.py")], str(tmp_path), "MATCH")

    assert len(matches) == 10


def test_search_invalid_regex_raises_value_error(tmp_path):
    _write(tmp_path / "a.py", "x\n")
    with pytest.raises(ValueError):
        search_files([str(tmp_path / "a.py")], str(tmp_path), "(unclosed")


def test_read_detail_range_full_text_by_default():
    text = "line1\nline2\nline3"
    assert read_detail_range(text) == text


def test_read_detail_range_slices_inclusive_1_based():
    text = "line1\nline2\nline3\nline4"
    assert read_detail_range(text, start_line=2, end_line=3) == "line2\nline3"


def test_read_detail_range_open_ended_bounds():
    text = "line1\nline2\nline3"
    assert read_detail_range(text, start_line=2) == "line2\nline3"
    assert read_detail_range(text, end_line=2) == "line1\nline2"


def test_read_detail_range_clamps_out_of_range_bounds():
    text = "line1\nline2"
    assert read_detail_range(text, start_line=0, end_line=100) == text
