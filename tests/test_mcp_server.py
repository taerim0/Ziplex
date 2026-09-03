"""Verifies the MCP tools are registered and callable through the actual
mcp.server.MCPServer API (name, docstring-as-description, call_tool
dispatch) -- not just as plain Python functions. The underlying logic
itself is already covered elsewhere: test_relationship.py exercises
get_dependents/get_blast_radius, test_search.py exercises search_files/
read_detail_range, and test_pack_integration.py produces the aif.json/
detail.json shape these tools read. This file only checks the MCP-specific
wiring on top of that.
"""

import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ziplex import freshness
from ziplex import mcp_server


def _call(name: str, arguments: dict):
    return asyncio.run(mcp_server.mcp.call_tool(name, arguments))


def _json_result(result) -> dict:
    """call_tool() doesn't populate structured_content for dict-returning
    tools (only for list-returning ones, as of this SDK version) -- the
    reliable path either way is the first text content block, which is
    always a JSON-serialized copy of the return value.
    """
    return json.loads(result.content[0].text)


def test_all_tools_are_registered():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "get_overview", "list_files", "get_folders", "get_relationships", "get_dependents",
        "get_blast_radius", "get_detail", "check_freshness", "search_project",
    }


def test_tool_descriptions_come_from_docstrings():
    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert "always-affordable" in tools["get_overview"].description
    assert "don't already know which file" in tools["search_project"].description


def _write_sample_aif(tmp_path):
    aif_path = tmp_path / "sample.json"
    aif_path.write_text(json.dumps({
        "project": {"name": "sample", "prompt": "A sample project."},
        "rules": ["rule one"],
        "tokens": {"GPT-4o": {"original": 100, "compressed": 20, "saved_pct": 80.0}},
        "files": {
            "a.py": {"summary": "does a thing"},
            "b.py": {"summary": "uses a.py"},
        },
        "folders": {
            ".": {"summary": "Top-level project files."},
        },
        "relationships": {
            "a.py": {"internal": [], "external": []},
            "b.py": {"internal": ["a.py"], "external": []},
        },
    }), encoding="utf-8")
    (tmp_path / "sample.detail.json").write_text(json.dumps({
        "a.py": {"compressed": "def thing():\n    ⋮----\n"},
        "b.py": {"compressed": "import a\n"},
    }), encoding="utf-8")
    return str(aif_path)


def test_get_overview_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("get_overview", {"aif_path": aif_path})

    assert result.is_error is False
    data = _json_result(result)
    assert data["project"]["name"] == "sample"
    assert data["file_count"] == 2


def test_list_files_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("list_files", {"aif_path": aif_path})

    assert result.is_error is False
    data = _json_result(result)
    assert data == {
        "a.py": {"summary": "does a thing", "confidence": 1.0},
        "b.py": {"summary": "uses a.py", "confidence": 1.0},
    }


def test_get_folders_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("get_folders", {"aif_path": aif_path})

    assert result.is_error is False
    data = _json_result(result)
    assert data == {".": {"summary": "Top-level project files."}}


def test_get_overview_omits_stale_field_when_project_path_not_given(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("get_overview", {"aif_path": aif_path})
    assert "_stale" not in _json_result(result)


def test_get_overview_attaches_stale_warning_when_project_changed(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "sample.cache.json").write_text(
        json.dumps(freshness.build_manifest([str(project / "a.py")], str(project))),
        encoding="utf-8",
    )

    (project / "a.py").write_text("x = 2\n", encoding="utf-8")  # edit after the cache was taken

    result = _call("get_overview", {"aif_path": aif_path, "project_path": str(project)})
    data = _json_result(result)
    assert data["_stale"] == {"is_stale": True, "changed": ["a.py"], "added": [], "removed": []}


def test_get_overview_no_stale_field_when_project_still_matches(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "sample.cache.json").write_text(
        json.dumps(freshness.build_manifest([str(project / "a.py")], str(project))),
        encoding="utf-8",
    )

    result = _call("get_overview", {"aif_path": aif_path, "project_path": str(project)})
    assert "_stale" not in _json_result(result)


def test_get_overview_stale_check_ignores_a_missing_cache_json(tmp_path):
    # no sample.cache.json written at all -- treated the same as "can't
    # check", not an error
    aif_path = _write_sample_aif(tmp_path)
    result = _call("get_overview", {"aif_path": aif_path, "project_path": str(tmp_path)})
    assert "_stale" not in _json_result(result)


def test_list_files_attaches_stale_warning_when_project_changed(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "sample.cache.json").write_text(
        json.dumps(freshness.build_manifest([str(project / "a.py")], str(project))),
        encoding="utf-8",
    )

    (project / "a.py").write_text("x = 2\n", encoding="utf-8")

    result = _call("list_files", {"aif_path": aif_path, "project_path": str(project)})
    data = _json_result(result)
    assert data["_stale"]["is_stale"] is True
    assert data["a.py"] == {"summary": "does a thing", "confidence": 1.0}  # real files untouched


def test_get_relationships_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("get_relationships", {"aif_path": aif_path})

    assert result.is_error is False
    data = _json_result(result)
    assert data == {
        "a.py": {"internal": [], "external": []},
        "b.py": {"internal": ["a.py"], "external": []},
    }


def test_get_relationships_files_param_scopes_the_result_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("get_relationships", {"aif_path": aif_path, "files": ["a.py"]})

    assert result.is_error is False
    assert _json_result(result) == {"a.py": {"internal": [], "external": []}}


def test_list_files_confidence_below_param_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("list_files", {"aif_path": aif_path, "confidence_below": 0.34})

    assert result.is_error is False
    # sample aif has no stored "confidence" -- defaults to 1.0, above any
    # real cutoff, so nothing is flagged.
    assert _json_result(result) == {}


def test_list_files_folder_param_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("list_files", {"aif_path": aif_path, "folder": "."})

    assert result.is_error is False
    assert set(_json_result(result)) == {"a.py", "b.py"}


def test_get_blast_radius_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("get_blast_radius", {"aif_path": aif_path, "file": "a.py"})

    assert result.is_error is False
    assert [c.text for c in result.content] == ["b.py"]


def test_search_project_via_call_tool(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("TARGET_TOKEN = 1\n", encoding="utf-8")

    result = _call("search_project", {"project_path": str(project), "pattern": "TARGET_TOKEN"})

    assert result.is_error is False
    data = _json_result(result)
    assert data["truncated"] is False
    assert [m["file"] for m in data["matches"]] == ["a.py"]


def test_get_dependents_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("get_dependents", {"aif_path": aif_path, "file": "a.py"})

    assert result.is_error is False
    # list[str] results come back as one plain-text content block per item
    # (not JSON-encoded, unlike list[dict] -- see search_project's own test
    # coverage in test_search.py for that shape via the plain function).
    assert [c.text for c in result.content] == ["b.py"]


def test_get_detail_via_call_tool(tmp_path):
    aif_path = _write_sample_aif(tmp_path)
    result = _call("get_detail", {"aif_path": aif_path, "file": "a.py"})

    assert result.is_error is False
    # read_detail_range() joins on "\n" after splitlines(), which normalizes
    # away a trailing newline -- expected, not a bug (see search.py).
    assert result.content[0].text == "def thing():\n    ⋮----"


def test_get_detail_missing_file_raises_tool_error(tmp_path):
    # get_detail() raises a plain ValueError on a missing file key.
    # MCPServer.call_tool() -- the low-level method this test (and _call())
    # uses directly -- re-raises that as ToolError rather than catching it;
    # that's a property of this convenience method specifically, not of the
    # server overall. A real client talking over stdio/HTTP goes through
    # _handle_call_tool() instead, which *does* catch any exception and
    # returns CallToolResult(is_error=True, ...) -- see
    # mcp/server/mcpserver/server.py in the installed SDK. So a bad
    # get_detail() call degrades gracefully for an actual MCP client; it
    # only raises here because of which API this test happens to call.
    aif_path = _write_sample_aif(tmp_path)
    with pytest.raises(ToolError):
        _call("get_detail", {"aif_path": aif_path, "file": "missing.py"})


def test_get_overview_uses_the_server_default_aif_when_omitted(tmp_path, monkeypatch):
    aif_path = _write_sample_aif(tmp_path)
    monkeypatch.setitem(mcp_server._defaults, "aif", aif_path)

    result = _call("get_overview", {})

    assert result.is_error is False
    assert _json_result(result)["project"]["name"] == "sample"


def test_explicit_aif_path_overrides_the_server_default(tmp_path, monkeypatch):
    default_aif = _write_sample_aif(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other_aif = _write_sample_aif(other_dir)
    monkeypatch.setitem(mcp_server._defaults, "aif", default_aif)

    result = _call("get_overview", {"aif_path": other_aif})

    assert result.is_error is False
    assert _json_result(result)["project"]["name"] == "sample"
    # Both fixtures happen to share a project name -- the real assertion is
    # that the call didn't silently fall back to the default path; confirm
    # by pointing the default somewhere that would raise if it were used.
    monkeypatch.setitem(mcp_server._defaults, "aif", str(tmp_path / "does-not-exist.json"))
    result2 = _call("get_overview", {"aif_path": other_aif})
    assert result2.is_error is False


def test_get_overview_raises_a_clear_error_when_aif_path_is_missing_and_no_default_is_set():
    # _defaults starts as {"aif": None, "project": None} for any test that
    # never calls main() or sets one via monkeypatch.
    assert mcp_server._defaults["aif"] is None
    with pytest.raises(ToolError):
        _call("get_overview", {})


def test_search_project_uses_the_server_default_project_when_omitted(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("TARGET_TOKEN = 1\n", encoding="utf-8")
    monkeypatch.setitem(mcp_server._defaults, "project", str(project))

    result = _call("search_project", {"pattern": "TARGET_TOKEN"})

    assert result.is_error is False
    data = _json_result(result)
    assert [m["file"] for m in data["matches"]] == ["a.py"]


def test_main_sets_defaults_from_cli_args(monkeypatch):
    monkeypatch.setattr(mcp_server.mcp, "run", lambda: None)
    monkeypatch.setattr(
        "sys.argv", ["ziplex-mcp", "--aif", "some/out.json", "--project", "some/project"]
    )
    monkeypatch.setitem(mcp_server._defaults, "aif", None)
    monkeypatch.setitem(mcp_server._defaults, "project", None)

    mcp_server.main()

    assert mcp_server._defaults["aif"] == "some/out.json"
    assert mcp_server._defaults["project"] == "some/project"


def test_check_freshness_via_call_tool(tmp_path):
    project = tmp_path / "project"
    (project).mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")

    aif_path = tmp_path / "sample.json"
    aif_path.write_text(json.dumps({"project": {"name": "sample"}}), encoding="utf-8")
    (tmp_path / "sample.cache.json").write_text(
        json.dumps(freshness.build_manifest([str(project / "a.py")], str(project))),
        encoding="utf-8",
    )

    fresh_result = _call("check_freshness", {"project_path": str(project), "aif_path": str(aif_path)})
    assert _json_result(fresh_result) == {
        "is_stale": False, "changed": [], "added": [], "removed": [], "unchanged_count": 1,
    }

    (project / "a.py").write_text("x = 2\n", encoding="utf-8")  # edit after the cache was taken

    stale_result = _call("check_freshness", {"project_path": str(project), "aif_path": str(aif_path)})
    assert _json_result(stale_result) == {
        "is_stale": True, "changed": ["a.py"], "added": [], "removed": [], "unchanged_count": 0,
    }
