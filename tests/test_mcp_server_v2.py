"""MCP v2 (default mode): the five graph tools over the live index, called
through MCPServer.call_tool like a client would. Query logic itself is
covered by tests/core/test_api.py; this file checks the MCP wiring, the
throttled refresh, error text and the context budget."""

import asyncio
import json
import subprocess
import threading

import pytest

from ziplex import mcp_server
from ziplex.core import api


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    for rel, text in {
        "pkg/__init__.py": "",
        "pkg/core.py": "import requests\n\ndef run():\n    return 1\n",
        "pkg/service.py": "from pkg.core import run\n\ndef serve():\n    return run()\n",
        "app.py": "from pkg.service import serve\n",
        "tests/test_service.py": "from pkg.service import serve\n\ndef test_serve():\n    assert serve()\n",
        "README.md": "Start with app.py.\n",
    }.items():
        _write(root, rel, text)
    return root


@pytest.fixture
def server(repo, tmp_path):
    return mcp_server.build_server("v2", project=str(repo), db_path=str(tmp_path / "i.db"))


def _call(server, name, arguments=None) -> str:
    result = asyncio.run(server.call_tool(name, arguments or {}))
    return result.content[0].text


def _tools_payload(server) -> str:
    tools = asyncio.run(server.list_tools())
    return json.dumps([t.model_dump(by_alias=True, exclude_none=True) for t in tools])


def test_v2_registers_exactly_the_five_graph_tools(server):
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == set(mcp_server.TOOL_DESCRIPTIONS_V2) == {
        "graph_overview", "who_depends_on", "dependencies_of", "impacted_tests", "dependency_path",
    }
    for t in asyncio.run(server.list_tools()):
        assert t.description == mcp_server.TOOL_DESCRIPTIONS_V2[t.name]
        assert len(t.description.split()) <= 60
    assert server.instructions == mcp_server.INSTRUCTIONS_V2


def test_v2_definitions_stay_under_1k_tokens(server):
    # Tool list + instructions are sent in every session. Measured at
    # writing: ~2.9K chars, ~720 o200k tokens.
    payload = _tools_payload(server)
    assert len(payload) + len(mcp_server.INSTRUCTIONS_V2) < 4000
    for tool in ("who_depends_on", "dependencies_of", "impacted_tests", "dependency_path", "graph_overview"):
        assert tool in mcp_server.INSTRUCTIONS_V2


def test_graph_overview(server):
    text = _call(server, "graph_overview")
    assert "code files (1 tests)" in text
    assert "most depended on:" in text
    assert text.splitlines()[-1].startswith("[index gen 1, fresh")


def test_who_depends_on_with_suffix_and_depth(server):
    text = _call(server, "who_depends_on", {"files": ["core.py"], "depth": 3})
    assert text.startswith("pkg/core.py <- 3 dependent(s) in 2 hop(s), 1 test(s)")
    assert "  hop1: pkg/service.py:1\n  hop2: app.py | tests/test_service.py\n" in text
    assert "README.md" not in text
    with_mentions = _call(server, "who_depends_on", {"files": ["app.py"], "include_mentions": True})
    assert "README.md" in with_mentions


def test_dependencies_of(server):
    text = _call(server, "dependencies_of", {"files": ["pkg/core.py"], "external": True})
    assert text.startswith("pkg/core.py -> 0 file(s)")
    assert "external: requests" in text
    assert "pkg/core.py:1" in _call(server, "dependencies_of", {"files": ["pkg/service.py"]})


def test_impacted_tests_from_files(server):
    text = _call(server, "impacted_tests", {"files": ["pkg/core.py"]})
    assert "tests affected: 1" in text
    assert "tests/test_service.py  (via pkg/service.py <- pkg/core.py)" in text
    assert "run: " in text


def test_impacted_tests_without_arguments_uses_the_worktree_diff(repo, server):
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("init", "-q")
    run("-c", "user.email=a@b", "-c", "user.name=a", "add", "-A")
    run("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init")
    _write(repo, "pkg/service.py", "from pkg.core import run\n\ndef serve():\n    return run() + 1\n")
    text = _call(server, "impacted_tests")
    assert text.startswith("changed: 1 file(s); tests affected: 1")
    bad = _call(server, "impacted_tests", {"diff": "no-such-rev"})
    assert bad.startswith("error: git")


def test_dependency_path(server):
    text = _call(server, "dependency_path", {"source": "app.py", "target": "core.py"})
    assert text.splitlines()[0] == (
        "2 hop(s): app.py -> pkg/service.py (line 1 of app.py) -> pkg/core.py (line 1 of pkg/service.py)"
    )
    none = _call(server, "dependency_path", {"source": "core.py", "target": "app.py"})
    assert none.startswith("no import path from pkg/core.py to app.py")


def test_unknown_and_ambiguous_files_come_back_as_error_text(repo, server, monkeypatch):
    text = _call(server, "who_depends_on", {"files": ["nope.py"]})
    assert text.startswith("error: 'nope.py' is not an indexed file")
    assert text.splitlines()[-1].startswith("[index gen")
    _write(repo, "other/core.py", "")
    monkeypatch.setattr(mcp_server, "REFRESH_INTERVAL", 0.0)
    assert "is ambiguous" in _call(server, "dependency_path", {"source": "app.py", "target": "core.py"})


def test_refresh_is_throttled_within_the_interval(server, monkeypatch):
    calls = []
    original = api.Graph.refresh
    monkeypatch.setattr(api.Graph, "refresh", lambda self, *a: calls.append(1) or original(self, *a))
    first = _call(server, "graph_overview")
    second = _call(server, "graph_overview")
    assert len(calls) == 1
    assert first.splitlines()[-1] == "[index gen 1, fresh]"
    assert second.splitlines()[-1].startswith("[index gen 1, fresh as of ")


def test_an_edit_is_seen_once_the_throttle_window_passes(repo, server, monkeypatch):
    assert "0 dependent(s)" in _call(server, "who_depends_on", {"files": ["app.py"]})
    _write(repo, "main.py", "import app\n")
    # Within the window: the cached index answers (main.py unknown yet).
    assert "0 dependent(s)" in _call(server, "who_depends_on", {"files": ["app.py"]})
    monkeypatch.setattr(mcp_server, "REFRESH_INTERVAL", 0.0)
    text = _call(server, "who_depends_on", {"files": ["app.py"]})
    assert "hop1: main.py:1" in text
    assert text.splitlines()[-1] == "[index gen 2, fresh]"


def test_a_call_during_a_slow_first_build_reports_busy(server, monkeypatch):
    release = threading.Event()
    original = api.Graph.refresh

    def slow_refresh(self, *a):
        release.wait(5)
        return original(self, *a)

    monkeypatch.setattr(api.Graph, "refresh", slow_refresh)
    monkeypatch.setattr(mcp_server, "INDEX_WAIT", 0.05)
    thread = server.ziplex_session.start_background_refresh()
    assert _call(server, "graph_overview") == mcp_server.BUSY_MESSAGE
    release.set()
    thread.join(5)
    assert _call(server, "graph_overview").splitlines()[-1].startswith("[index gen 1, fresh")


def test_main_defaults_to_v2_on_the_given_project(repo, monkeypatch):
    served = []
    monkeypatch.setattr(mcp_server.MCPServer, "run", lambda self: served.append(self))
    mcp_server.main(["--project", str(repo)])
    server = served[0]
    assert server.instructions == mcp_server.INSTRUCTIONS_V2
    assert server.ziplex_session.graph.root == str(repo.resolve())
    # main() started the first build in the background; a call waits for it.
    assert _call(server, "graph_overview").splitlines()[-1].startswith("[index gen 1, fresh")
    server.ziplex_session.graph.index.close()


@pytest.mark.parametrize("argv", [["--aif", "x.json"], ["--legacy-tools"]])
def test_main_selects_legacy_mode(argv, monkeypatch):
    served = []
    monkeypatch.setattr(mcp_server.MCPServer, "run", lambda self: served.append(self))
    monkeypatch.setitem(mcp_server._defaults, "aif", None)
    monkeypatch.setitem(mcp_server._defaults, "project", None)
    mcp_server.main(argv)
    names = {t.name for t in asyncio.run(served[0].list_tools())}
    assert names == set(mcp_server.TOOL_DESCRIPTIONS)
