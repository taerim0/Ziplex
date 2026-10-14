from pathlib import Path

import pytest

from ziplex.file.textutil import relative_key


@pytest.mark.parametrize("fp,root", [
    (r"C:\a\b\c.py", r"C:\a"), ("C:/a/b/c.py", "C:/a/"), (r"C:\a\b\..\c.py", r"C:\a"),
    (r"C:\a\.\b.py", r"C:\a"), (r"C:\A\b.py", r"C:\a"), ("x/y.py", "."), ("./x/y.py", "."),
    (r"C:\ab\c.py", r"C:\a"), ("C:/a//b.py", "C:/a"), ("/r/x.py", "/r"), ("/x.py", "/"),
    (r"C:\a\b c\d.py", r"C:\a"),
])
def test_relative_key_fast_path_matches_pathlib(fp, root):
    try:
        expected = Path(fp).relative_to(Path(root)).as_posix()
    except ValueError:
        expected = Path(fp).name
    assert relative_key(fp, root) == expected


def test_cached_reads_memoizes_only_inside_the_call(tmp_path):
    from ziplex.file.textutil import cached_reads, read_text

    f = tmp_path / "a.txt"
    f.write_text("one", encoding="utf-8")

    @cached_reads
    def read_twice():
        first = read_text(str(f))
        f.write_text("two", encoding="utf-8")
        return first, read_text(str(f))

    assert read_twice() == ("one", "one")
    assert read_text(str(f)) == "two"
