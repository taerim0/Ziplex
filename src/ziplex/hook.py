"""Claude Code PostToolUse hook (`ziplex hook post-edit`, docs/REVIEW-2026-10-14.md §5.6).

Contract:
- reads the hook JSON from stdin (tool_input.file_path / notebook_path,
  session_id, cwd);
- acts only inside a project that already has `.ziplex/index.db` (nearest
  ancestor of the edited file) -- silent everywhere else;
- refreshes just that file, then reports its direct dependents and impacted
  tests as `additionalContext`, under MAX_CHARS, at most once per
  (session, file) per REPEAT_SECONDS;
- never blocks and never fails: every error is swallowed, exit code is 0.

Imports stay stdlib-only at module level; the graph engine is imported only
once a project index is found.
"""
import json
import os
import time
from pathlib import Path

INDEX_REL = Path(".ziplex") / "index.db"
STATE_REL = Path(".ziplex") / "hook_state.json"
MAX_CHARS = 300
REPEAT_SECONDS = 600
TOP_DEPENDENTS = 3


def find_project_root(file_path: str) -> Path | None:
    """Nearest ancestor directory of `file_path` holding `.ziplex/index.db`."""
    for folder in Path(file_path).resolve().parents:
        if (folder / INDEX_REL).is_file():
            return folder
    return None


def _edited_path(payload: dict) -> str | None:
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path or not isinstance(path, str):
        return None
    if not os.path.isabs(path):
        path = os.path.join(payload.get("cwd") or os.getcwd(), path)
    return path


def _seen_recently(root: Path, key: str, now: float) -> bool:
    """True if `key` was reported within REPEAT_SECONDS; otherwise records it.
    The state file is pruned of expired keys on every write."""
    state_path = root / STATE_REL
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    state = {k: v for k, v in state.items() if isinstance(v, (int, float)) and now - v < REPEAT_SECONDS}
    if key in state:
        return True
    state[key] = now
    tmp = state_path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, state_path)
    except OSError:
        tmp.unlink(missing_ok=True)
    return False


def build_message(rel: str, dependents: list[str], tests: int, command: str | None) -> str:
    """One line under MAX_CHARS. A run command that doesn't fit is replaced
    by the equivalent `ziplex impact` call rather than truncated."""
    head = f"ziplex: {rel} has {len(dependents)} dependent(s)"
    if dependents:
        shown = ", ".join(dependents[:TOP_DEPENDENTS])
        more = f" +{len(dependents) - TOP_DEPENDENTS}" if len(dependents) > TOP_DEPENDENTS else ""
        head += f" ({shown}{more})"
    tail = f"; {tests} test(s) affected"
    for cmd in (command, f"ziplex impact {rel}"):
        if cmd and tests:
            msg = f"{head}{tail} -> {cmd}"
            if len(msg) < MAX_CHARS:
                return msg
    msg = head + tail
    return msg if len(msg) < MAX_CHARS else msg[: MAX_CHARS - 5] + " ..."


def post_edit(payload: dict, now: float | None = None) -> dict | None:
    """The hook output dict, or None when there is nothing worth saying."""
    path = _edited_path(payload)
    if path is None:
        return None
    root = find_project_root(path)
    if root is None or (root / ".ziplex") in Path(path).resolve().parents:
        return None

    from .core.api import Graph  # deferred: only paid inside an indexed project

    graph = Graph(str(root))
    try:
        graph.refresh([path])
        try:
            rel = graph.resolve(path)
        except ValueError:  # ignored / out-of-scope / deleted file
            return None
        hops = graph.dependents([rel], depth=1)[rel]
        dependents = [p for p, _ in hops[0]] if hops else []
        impact = graph.impacted_tests([rel])
    finally:
        graph.index.close()

    tests = len(impact["tests"])
    if not dependents and not tests:
        return None
    now = time.time() if now is None else now
    if _seen_recently(root, f"{payload.get('session_id') or ''}|{rel}", now):
        return None
    command = impact["commands"][0] if impact["commands"] else None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": build_message(rel, dependents, tests, command),
        }
    }


def run(stdin, stdout) -> int:
    """Entry point for `ziplex hook post-edit`. Always returns 0."""
    try:
        payload = json.loads(stdin.read() or "{}")
        if isinstance(payload, dict):
            out = post_edit(payload)
            if out:
                stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
                stdout.flush()
    except Exception:  # a hook must never break the editing session
        pass
    return 0
