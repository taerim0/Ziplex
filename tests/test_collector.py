from pathlib import Path

from ziplex.file.collector import collect_files


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_collects_plain_text_files(tmp_path):
    _write(tmp_path / "main.py", "print('hi')\n")
    _write(tmp_path / "README.md", "# hello\n")

    collected = {Path(f).relative_to(tmp_path).as_posix() for f in collect_files(str(tmp_path))}
    assert collected == {"main.py", "README.md"}


def test_skips_default_ignore_directories(tmp_path):
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    _write(tmp_path / "node_modules" / "pkg" / "index.js", "module.exports = {};\n")
    _write(tmp_path / ".gradle" / "cache.properties", "k=v\n")

    collected = {Path(f).relative_to(tmp_path).as_posix() for f in collect_files(str(tmp_path))}
    assert collected == {"src/app.py"}


def test_respects_project_gitignore(tmp_path):
    _write(tmp_path / ".gitignore", "secrets/\n*.local\n")
    _write(tmp_path / "app.py", "x = 1\n")
    _write(tmp_path / "secrets" / "keys.txt", "shh\n")
    _write(tmp_path / "notes.local", "private\n")

    collected = {Path(f).relative_to(tmp_path).as_posix() for f in collect_files(str(tmp_path))}
    # .gitignore itself isn't matched by its own patterns, so it's collected too
    assert collected == {"app.py", ".gitignore"}


def test_skips_files_that_are_not_decodable_as_text(tmp_path):
    _write(tmp_path / "app.py", "x = 1\n")
    (tmp_path / "sprite.bin").write_bytes(bytes(range(256)))  # not valid utf-8

    collected = {Path(f).relative_to(tmp_path).as_posix() for f in collect_files(str(tmp_path))}
    assert collected == {"app.py"}


def test_include_pattern_scopes_to_only_matching_files(tmp_path):
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    _write(tmp_path / "README.md", "# hello\n")

    collected = {Path(f).relative_to(tmp_path).as_posix() for f in collect_files(str(tmp_path), include=["src/**/*.py"])}
    assert collected == {"src/app.py"}


def test_include_pattern_does_not_resurrect_a_default_ignored_file(tmp_path):
    # an include pattern narrows the candidate set further -- it can never
    # override DEFAULT_IGNORE/.gitignore, which are applied first
    _write(tmp_path / "node_modules" / "pkg" / "index.js", "module.exports = {};\n")
    _write(tmp_path / "src" / "app.js", "x = 1;\n")

    collected = {Path(f).relative_to(tmp_path).as_posix() for f in collect_files(str(tmp_path), include=["**/*.js"])}
    assert collected == {"src/app.js"}


def test_extra_ignore_pattern_excludes_beyond_defaults(tmp_path):
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    _write(tmp_path / "src" / "app.generated.py", "x = 1\n")

    collected = {Path(f).relative_to(tmp_path).as_posix() for f in collect_files(str(tmp_path), ignore=["*.generated.py"])}
    assert collected == {"src/app.py"}


def test_no_include_patterns_keeps_default_behavior(tmp_path):
    # an empty list, same as None, must not be mistaken for "include
    # nothing" -- both mean "no include filter at all"
    _write(tmp_path / "a.py", "x = 1\n")

    collected = {Path(f).relative_to(tmp_path).as_posix() for f in collect_files(str(tmp_path), include=[])}
    assert collected == {"a.py"}
