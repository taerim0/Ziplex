import io
import json

import pytest

from ziplex import hook
from ziplex.core.api import Graph


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/core.py", "def run():\n    return 1\n")
    _write(root, "pkg/service.py", "from pkg.core import run\n\ndef serve():\n    return run()\n")
    _write(root, "pkg/lonely.py", "X = 1\n")
    _write(root, "tests/test_service.py", "from pkg.service import serve\n\ndef test_serve():\n    assert serve()\n")
    graph = Graph(str(root))
    graph.refresh()
    graph.index.close()
    return root


def _payload(root, rel, session="s1", **extra):
    return {"tool_name": "Edit", "session_id": session, "cwd": str(root),
            "tool_input": {"file_path": str(root / rel), **extra}}


def _context(out):
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    return out["hookSpecificOutput"]["additionalContext"]


def test_reports_dependents_and_tests_once_per_session_and_file(project):
    out = hook.post_edit(_payload(project, "pkg/core.py"), now=1000.0)
    text = _context(out)
    assert text.startswith("ziplex: pkg/core.py has 1 dependent(s) (pkg/service.py)")
    assert "1 test(s) affected -> pytest tests/test_service.py" in text
    assert len(text) < hook.MAX_CHARS

    assert hook.post_edit(_payload(project, "pkg/core.py"), now=1100.0) is None          # same session+file
    assert hook.post_edit(_payload(project, "pkg/core.py", "s2"), now=1100.0) is not None  # other session
    assert hook.post_edit(_payload(project, "pkg/core.py"), now=1000.0 + hook.REPEAT_SECONDS + 1) is not None


def test_state_file_is_pruned_of_expired_entries(project):
    hook.post_edit(_payload(project, "pkg/core.py", "old"), now=1000.0)
    hook.post_edit(_payload(project, "pkg/service.py", "new"), now=1000.0 + hook.REPEAT_SECONDS + 5)
    state = json.loads((project / ".ziplex" / "hook_state.json").read_text(encoding="utf-8"))
    assert list(state) == ["new|pkg/service.py"]


def test_refreshes_the_edited_file_before_answering(project):
    _write(project, "tests/test_lonely.py", "from pkg.lonely import X\n")
    text = _context(hook.post_edit(_payload(project, "tests/test_lonely.py"), now=1.0))
    assert "0 dependent(s); 1 test(s) affected -> pytest tests/test_lonely.py" in text
    text = _context(hook.post_edit(_payload(project, "pkg/lonely.py"), now=1.0))
    assert "1 dependent(s) (tests/test_lonely.py)" in text


def test_silent_when_nothing_depends_on_the_file(project):
    assert hook.post_edit(_payload(project, "pkg/lonely.py"), now=1.0) is None


def test_silent_outside_an_indexed_project(tmp_path):
    _write(tmp_path / "plain", "a.py", "import b\n")
    assert hook.post_edit(_payload(tmp_path / "plain", "a.py"), now=1.0) is None
    assert not (tmp_path / "plain" / ".ziplex").exists()


def test_notebook_path_and_relative_path_are_accepted(project):
    payload = {"session_id": "nb", "cwd": str(project), "tool_input": {"notebook_path": "pkg/core.py"}}
    assert hook.post_edit(payload, now=1.0) is not None


def test_ignores_files_inside_the_index_dir_and_unknown_files(project):
    assert hook.post_edit(_payload(project, ".ziplex/hook_state.json"), now=1.0) is None
    assert hook.post_edit(_payload(project, "pkg/missing.py"), now=1.0) is None


def test_run_never_fails_and_always_returns_zero(project, monkeypatch):
    out = io.StringIO()
    assert hook.run(io.StringIO("not json"), out) == 0
    assert hook.run(io.StringIO(""), out) == 0
    assert out.getvalue() == ""

    def boom(payload):
        raise RuntimeError("index exploded")
    monkeypatch.setattr(hook, "post_edit", boom)
    assert hook.run(io.StringIO(json.dumps(_payload(project, "pkg/core.py"))), out) == 0
    assert out.getvalue() == ""


def test_run_writes_the_hook_json(project):
    out = io.StringIO()
    hook.run(io.StringIO(json.dumps(_payload(project, "pkg/core.py", "run"))), out)
    assert "pkg/service.py" in _context(json.loads(out.getvalue()))


def test_long_commands_fall_back_to_ziplex_impact():
    many = [f"pkg/dependent_module_number_{i}.py" for i in range(10)]
    text = hook.build_message("pkg/core.py", many, 40, "pytest " + " ".join(f"tests/test_{i}.py" for i in range(40)))
    assert len(text) < hook.MAX_CHARS
    assert text.endswith("-> ziplex impact pkg/core.py")
    assert "+7)" in text


def test_message_never_exceeds_the_limit():
    text = hook.build_message("x" * 400, ["y" * 400], 3, "pytest t.py")
    assert len(text) < hook.MAX_CHARS
