"""scan_file()/scan_files() -- the pattern-based fallback specifically,
since secretlint itself either isn't installed in most environments or (on
Windows, see _scan_with_secretlint()'s own docstring) never actually runs;
every real invocation in this suite exercises the fallback path.
"""

from ziplex.file.scanner import scan_file, scan_files


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_file_returns_none_for_a_clean_file(tmp_path):
    path = tmp_path / "main.py"
    _write(path, "def add(a, b):\n    return a + b\n")
    assert scan_file(str(path)) is None


def test_scan_file_reports_the_matched_line_not_the_whole_file(tmp_path):
    path = tmp_path / "secret.env"
    _write(path, 'NORMAL=fine\nAPI_KEY = "abc123"\nOTHER=fine\n')

    result = scan_file(str(path))

    assert result["line"] == 2
    assert result["matched_text"] == 'API_KEY = "abc123"'
    assert "API_KEY" in result["reason"]


def test_scan_file_matches_the_first_triggering_line(tmp_path):
    # two lines would both match different patterns -- the first one found
    # wins, not an arbitrary one, so the reported line is deterministic
    path = tmp_path / "multi.env"
    _write(path, 'PASSWORD = "x"\nAPI_KEY = "y"\n')

    result = scan_file(str(path))
    assert result["line"] == 1
    assert "PASSWORD" in result["reason"]


def test_scan_files_splits_safe_and_dangerous_with_reasons(tmp_path):
    _write(tmp_path / "main.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path / "secret.env", 'API_KEY = "abc123"\n')

    result = scan_files([str(tmp_path / "main.py"), str(tmp_path / "secret.env")])

    assert result["safe"] == [str(tmp_path / "main.py")]
    assert len(result["dangerous"]) == 1
    entry = result["dangerous"][0]
    assert entry["file"] == str(tmp_path / "secret.env")
    assert entry["line"] == 1
    assert entry["matched_text"] == 'API_KEY = "abc123"'


def test_scan_file_falls_back_to_pattern_when_secretlint_is_unavailable(tmp_path, monkeypatch):
    from ziplex.file import scanner

    monkeypatch.setattr(scanner, "_scan_with_secretlint", lambda path: None)
    path = tmp_path / "secret.env"
    _write(path, 'API_KEY = "abc123"\n')

    result = scan_file(str(path))
    assert result is not None
    assert result["matched_text"] == 'API_KEY = "abc123"'


def test_scan_file_trusts_a_clean_secretlint_result_without_falling_back(tmp_path, monkeypatch):
    # secretlint returning False (ran, found nothing) must NOT trigger the
    # pattern fallback -- only None (secretlint itself couldn't run) should.
    # A file that would otherwise match a fallback pattern stays "safe" here
    # specifically to prove the False/None distinction is respected.
    from ziplex.file import scanner

    monkeypatch.setattr(scanner, "_scan_with_secretlint", lambda path: False)
    path = tmp_path / "secret.env"
    _write(path, 'API_KEY = "abc123"\n')

    assert scan_file(str(path)) is None
