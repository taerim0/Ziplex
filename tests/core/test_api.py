import subprocess

import pytest

from ziplex.core import api


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    files = {
        "pkg/__init__.py": "",
        "pkg/core.py": "def run():\n    return 1\n",
        "pkg/service.py": "from pkg.core import run\n\ndef serve():\n    return run()\n",
        "app.py": "from pkg.service import serve\n",
        "tests/conftest.py": "from pkg import core\n",
        "tests/test_service.py": "from pkg.service import serve\n\ndef test_serve():\n    assert serve()\n",
        "tests/test_misc.py": "def test_nothing():\n    assert True\n",
        "README.md": "Start with app.py.\n",
        "go.mod": "module example.com/m\n",
        "db/db.go": "package db\n\nfunc Open() {}\n",
        "db/db_test.go": "package db\n\nimport \"testing\"\n\nfunc TestOpen(t *testing.T) { Open() }\n",
    }
    for rel, text in files.items():
        _write(root, rel, text)
    graph = api.Graph(str(root), db_path=str(tmp_path / "i.db"))
    graph.refresh()
    return root, graph


def test_dependents_by_hop_with_lines_and_no_prose_mentions(repo):
    _, g = repo
    result = g.dependents(["core.py"], depth=3)
    hops = result["pkg/core.py"]
    assert ("pkg/service.py", 1) in hops[0]
    assert ("tests/conftest.py", 1) in hops[0]
    flat = {p for hop in hops for p, _ in hop}
    assert {"app.py", "tests/test_service.py", "tests/test_misc.py"} <= flat  # conftest reaches every test below it
    assert "README.md" not in flat
    assert "README.md" in {p for hop in g.dependents(["app.py"], include_mentions=True)["app.py"] for p, _ in hop}


def test_path_and_dependencies(repo):
    _, g = repo
    assert g.path("app.py", "pkg/core.py") == [("app.py", "pkg/service.py", 1), ("pkg/service.py", "pkg/core.py", 1)]
    assert g.path("pkg/core.py", "app.py") is None
    assert g.dependencies(["pkg/service.py"])["pkg/service.py"]["internal"] == [("pkg/core.py", 1)]


def test_impacted_tests_follow_imports_conftest_and_go_packages(repo):
    _, g = repo
    result = g.impacted_tests(["pkg/core.py", "db/db.go"])
    tests = {t["file"]: t["chain"] for t in result["tests"]}
    assert set(tests) == {"tests/test_service.py", "tests/test_misc.py", "db/db_test.go"}
    assert tests["tests/test_misc.py"] == ["tests/test_misc.py", "tests/conftest.py", "pkg/core.py"]
    assert result["untested"] == []
    assert any(c.startswith("pytest ") for c in result["commands"])
    assert "go test ./db" in result["commands"]


def test_impacted_tests_reports_untested_files_and_run_all_triggers(repo):
    _, g = repo
    result = g.impacted_tests(["app.py", "go.mod"])
    assert result["untested"] == ["app.py"]
    assert result["run_all"] == ["go.mod"]


def test_impacted_tests_from_a_git_worktree_diff(repo):
    root, g = repo
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)
    run("init", "-q")
    run("-c", "user.email=a@b", "-c", "user.name=a", "add", "-A")
    run("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init")
    _write(root, "pkg/service.py", "from pkg.core import run\n\ndef serve():\n    return run() + 1\n")
    g.refresh()
    result = g.impacted_tests(diff="worktree")
    assert result["changed"] == ["pkg/service.py"]
    assert [t["file"] for t in result["tests"]] == ["tests/test_service.py"]


def test_resolve_accepts_suffixes_and_rejects_ambiguity(repo, tmp_path):
    root, g = repo
    assert g.resolve(str(root / "pkg" / "core.py")) == "pkg/core.py"
    assert g.resolve(r"pkg\service.py") == "pkg/service.py"
    _write(root, "other/core.py", "x = 1\n")
    g.refresh()
    with pytest.raises(ValueError, match="ambiguous"):
        g.resolve("core.py")
    with pytest.raises(ValueError, match="not an indexed file"):
        g.resolve("nope.py")


def test_renderers_are_compact_text(repo):
    _, g = repo
    text = api.render_impacted(g.impacted_tests(["pkg/core.py"]))
    assert text.startswith("changed: 1 file(s); tests affected: 2")
    assert "run: pytest" in text
    assert "pkg/core.py <-" in api.render_dependents(g.dependents(["pkg/core.py"]))
    assert "code files" in api.render_overview(g.overview())


def test_overview_scope_limits_the_test_count(repo):
    _, g = repo
    assert g.overview("pkg")["tests"] == 0
    assert g.overview("tests")["tests"] == 2
