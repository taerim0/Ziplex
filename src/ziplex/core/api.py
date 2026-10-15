"""Graph queries over a GraphIndex, plus compact text renderers shared by the
CLI, the MCP server and the editor hook. Every query refreshes the index
first (`fresh=True`), so answers always match the disk."""
import json
import posixpath
from collections import deque
from dataclasses import dataclass, field

from ..file.relationship import graph_summary
from .diff import changed_files
from .index import GraphIndex
from .testmap import forces_run_all, implicit_dependencies, is_test_path, package_inits, run_command


# Imports that never run when a test imports the module (`if TYPE_CHECKING:`,
# TS `import type`, `if __name__ == "__main__":`). Still real dependencies,
# but they can't make a test fail at runtime -- impacted_tests skips them.
_INERT_CONTEXTS = {"typing", "main"}
_DEPENDENCY_KINDS = ("import", "lazy", "inert", "implicit")


@dataclass
class _Graph:
    generation: str
    files: dict[str, dict]                                   # path -> {"kind", "is_test", "flagged"}
    out: dict[str, list[tuple[str, int | None, str]]] = field(default_factory=dict)   # src -> [(dst, line, kind)]
    rev: dict[str, list[tuple[str, int | None, str]]] = field(default_factory=dict)   # dst -> [(src, line, kind)]
    external: dict[str, list[tuple[str, int | None]]] = field(default_factory=dict)


class Graph:
    """Read side of the index. Cheap to keep around: the in-memory adjacency
    is rebuilt only when the index generation changes."""

    def __init__(self, project_path: str, db_path: str | None = None):
        self.index = GraphIndex(project_path, db_path=db_path)
        self.root = self.index.root
        self._cache: _Graph | None = None

    def refresh(self, paths: list[str] | None = None) -> dict:
        return self.index.update(paths)

    def _graph(self) -> _Graph:
        generation = self.index.meta("generation") or "0"
        if self._cache and self._cache.generation == generation:
            return self._cache
        conn = self.index.conn
        # is_test is derived on load, not trusted from the row: the test-file
        # rules can change without the file's content changing.
        files = {
            path: {"kind": kind, "is_test": kind == "code" and is_test_path(path), "flagged": bool(flagged)}
            for path, kind, flagged in conn.execute(
                "SELECT path, kind, flagged FROM files WHERE kind IN ('code', 'text', 'media')"
            )
        }
        g = _Graph(generation, files)
        query = (
            "SELECT s.path, d.path, e.external, e.kind, e.line, e.ctx FROM edges e JOIN files s ON s.id = e.src_id "
            "LEFT JOIN files d ON d.id = e.dst_id ORDER BY s.path, e.line"
        )
        for src, dst, external, kind, line, ctx in conn.execute(query):
            if kind == "import" and ctx in _INERT_CONTEXTS:
                kind = "inert"
            elif kind == "import" and ctx == "function":
                kind = "lazy"
            if dst is None:
                g.external.setdefault(src, []).append((external, line))
                continue
            g.out.setdefault(src, []).append((dst, line, kind))
            g.rev.setdefault(dst, []).append((src, line, kind))
        paths = set(files)
        # "package" edges (module -> enclosing __init__.py) count for impact,
        # not as dependents: every module of a package would otherwise
        # "depend on" its __init__.py.
        for module in (p for p in files if p.endswith(".py")):
            for init in package_inits(module, paths):
                g.out.setdefault(module, []).append((init, None, "package"))
                g.rev.setdefault(init, []).append((module, None, "package"))
        for test in (p for p, info in files.items() if info["is_test"]):
            for dep in implicit_dependencies(test, paths):
                g.out.setdefault(test, []).append((dep, None, "implicit"))
                g.rev.setdefault(dep, []).append((test, None, "implicit"))
        self._cache = g
        return g

    # ---- path arguments -------------------------------------------------

    def resolve(self, arg: str) -> str:
        """Repo-relative path from a relative/absolute/backslashed path or a
        unique suffix ("query_service.py", "core/graph.py")."""
        files = self._graph().files
        value = arg.strip().replace("\\", "/")
        root = self.root.replace("\\", "/").rstrip("/") + "/"
        if value.lower().startswith(root.lower()):
            value = value[len(root):]
        if value not in files:
            value = posixpath.normpath(value[2:] if value.startswith("./") else value)
        if value in files:
            return value
        matches = sorted(p for p in files if p.endswith("/" + value))
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError(f"'{arg}' is ambiguous: {', '.join(matches[:5])}" + (" ..." if len(matches) > 5 else ""))
        raise ValueError(f"'{arg}' is not an indexed file of {self.root}")

    # ---- queries ---------------------------------------------------------

    def dependents(self, files: list[str], depth: int = 1, include_mentions: bool = False) -> dict:
        """Reverse dependencies grouped by hop. {file: [[(path, line), ...] per hop]}"""
        g = self._graph()
        kinds = set(_DEPENDENCY_KINDS) | ({"text"} if include_mentions else set())
        result = {}
        for arg in files:
            start = self.resolve(arg)
            seen, frontier, hops = {start}, [start], []
            for _ in range(max(1, depth)):
                layer = []
                for node in frontier:
                    for src, line, kind in g.rev.get(node, []):
                        if kind in kinds and src not in seen:
                            seen.add(src)
                            layer.append((src, line if node == start else None))
                if not layer:
                    break
                hops.append(sorted(layer))
                frontier = [p for p, _ in layer]
            result[start] = hops
        return result

    def dependencies(self, files: list[str], external: bool = False) -> dict:
        g = self._graph()
        result = {}
        for arg in files:
            path = self.resolve(arg)
            entry = {"internal": list(dict.fromkeys(
                (dst, line) for dst, line, kind in g.out.get(path, []) if kind in _DEPENDENCY_KINDS
            ))}
            mentions = [dst for dst, _, kind in g.out.get(path, []) if kind == "text"]
            if mentions:
                entry["mentions"] = mentions
            if external:
                entry["external"] = list(dict.fromkeys(g.external.get(path, [])))
            result[path] = entry
        return result

    def path(self, source: str, target: str) -> list[tuple[str, str, int | None]] | None:
        """Shortest import chain source -> ... -> target as (from, to, line) hops."""
        g = self._graph()
        a, b = self.resolve(source), self.resolve(target)
        parent: dict[str, tuple[str, int | None]] = {a: ("", None)}
        queue = deque([a])
        while queue:
            node = queue.popleft()
            if node == b:
                hops = []
                while node != a:
                    prev, line = parent[node]
                    hops.append((prev, node, line))
                    node = prev
                return list(reversed(hops))
            for dst, line, kind in g.out.get(node, []):
                if kind in _DEPENDENCY_KINDS and dst not in parent:
                    parent[dst] = (node, line)
                    queue.append(dst)
        return None

    def _impact_closure(self, changed: list[str]) -> dict[str, str | None]:
        """{file: next hop toward a changed file} for every file whose code
        can run a changed file. Reverse edges, except prose mentions and
        inert (TYPE_CHECKING / __main__) imports. A path that went through a
        function-local ("lazy") import may not continue over a package edge:
        importing a package member runs the package's __init__.py, but not
        the functions that __init__.py merely defines."""
        g = self._graph()
        parent: dict[str, str | None] = {c: None for c in changed}
        lazy = {c: False for c in changed}
        queue = deque(changed)
        while queue:
            node = queue.popleft()
            for src, _, kind in g.rev.get(node, []):
                if kind in ("text", "inert") or (kind == "package" and lazy[node]):
                    continue
                src_lazy = lazy[node] or kind == "lazy"
                # first visit, or a non-lazy path to a file seen only lazily
                if src not in lazy or (lazy[src] and not src_lazy):
                    parent[src] = node
                    lazy[src] = src_lazy
                    queue.append(src)
        return parent

    def impacted_tests(self, files: list[str] | None = None, diff: str | None = None) -> dict:
        """Tests reached from the changed files over reverse import (and
        implicit conftest / Go same-package) edges, with the shortest chain."""
        g = self._graph()
        changed, deleted, unknown = [], [], []
        if diff:
            changes = changed_files(self.root, diff)
            deleted = changes.deleted
            candidates = changes.modified
        else:
            candidates = files or []
        for arg in candidates:
            try:
                changed.append(self.resolve(arg))
            except ValueError:
                unknown.append(arg)

        run_all = sorted({p for p in changed + deleted + unknown if forces_run_all(p)})
        parent = self._impact_closure(changed)

        def chain(node: str) -> list[str]:
            out = [node]
            while parent[out[-1]] is not None:
                out.append(parent[out[-1]])
            return out

        tests = sorted(
            ({"file": p, "chain": chain(p)} for p in parent if g.files.get(p, {}).get("is_test")),
            key=lambda t: (len(t["chain"]), t["file"]),
        )
        def reaches_a_test(start: str) -> bool:
            # Checked per file: the shortest chains above can all start at a
            # changed test, hiding that it also covers another changed file.
            return any(g.files.get(p, {}).get("is_test") for p in self._impact_closure([start]) if p != start)

        untested = [
            c for c in changed
            if g.files[c]["kind"] == "code" and not g.files[c]["is_test"] and not reaches_a_test(c)
        ]
        package_json = None
        try:
            with open(posixpath.join(self.root, "package.json"), encoding="utf-8") as f:
                package_json = json.load(f)
        except (OSError, ValueError):
            pass
        return {
            "changed": changed,
            "deleted": deleted,
            "not_indexed": unknown,
            "tests": tests,
            "untested": untested,
            "run_all": run_all + (["deleted files: their importers can't be traced"] if deleted else []),
            "commands": run_command([t["file"] for t in tests], package_json),
        }

    def overview(self, scope: str = "", top_n: int = 10) -> dict:
        rel = self.index.relationships()
        prefix = scope.strip("/").replace("\\", "/")
        code = {p for p, info in self._graph().files.items() if info["kind"] == "code"}
        summary = graph_summary(
            rel, include_text_refs=False, top_n=top_n,
            scope=lambda n: n in code and (not prefix or n == prefix or n.startswith(prefix + "/")),
        )
        summary["tests"] = sum(
            1 for p in code
            if self._graph().files[p]["is_test"] and (not prefix or p == prefix or p.startswith(prefix + "/"))
        )
        unresolved: dict[str, int] = {}
        for src, ext in self._graph().external.items():
            if not prefix or src.startswith(prefix + "/"):
                for name, _ in ext:
                    if name.startswith("."):  # a relative import that matched no file
                        unresolved[name] = unresolved.get(name, 0) + 1
        summary["unresolved_local_imports"] = sorted(unresolved.items(), key=lambda x: -x[1])[:top_n]
        return summary


# ---- text renderers (MCP / CLI / hook) -------------------------------------

def _loc(path: str, line: int | None) -> str:
    return f"{path}:{line}" if line else path


def render_dependents(result: dict, limit: int = 60) -> str:
    lines = []
    for target, hops in result.items():
        total = sum(len(h) for h in hops)
        tests = sum(1 for h in hops for p, _ in h if _looks_test(p))
        lines.append(f"{target} <- {total} dependent(s) in {len(hops)} hop(s), {tests} test(s)")
        shown = 0
        for i, hop in enumerate(hops, 1):
            items = hop[: max(0, limit - shown)]
            shown += len(items)
            more = f" ... +{len(hop) - len(items)}" if len(items) < len(hop) else ""
            lines.append(f"  hop{i}: " + " | ".join(_loc(p, l) for p, l in items) + more)
    return "\n".join(lines)


def _looks_test(path: str) -> bool:
    from .testmap import is_test_path
    return is_test_path(path)


def render_dependencies(result: dict) -> str:
    lines = []
    for path, entry in result.items():
        lines.append(f"{path} -> {len(entry['internal'])} file(s)")
        if entry["internal"]:
            lines.append("  " + " | ".join(_loc(p, l) for p, l in entry["internal"]))
        if entry.get("external"):
            lines.append("  external: " + ", ".join(sorted({n for n, _ in entry["external"]})))
        if entry.get("mentions"):
            lines.append("  mentions: " + ", ".join(entry["mentions"]))
    return "\n".join(lines)


def render_path(source: str, target: str, hops) -> str:
    if hops is None:
        return f"no import path from {source} to {target}"
    return f"{len(hops)} hop(s): " + " -> ".join(
        [hops[0][0]] + [f"{dst} (line {line} of {src})" if line else dst for src, dst, line in hops]
    )


def render_impacted(result: dict, limit: int = 60) -> str:
    lines = [f"changed: {len(result['changed'])} file(s); tests affected: {len(result['tests'])}"]
    for t in result["tests"][:limit]:
        via = " <- ".join(t["chain"][1:])
        lines.append(f"  {t['file']}" + (f"  (via {via})" if via else ""))
    if len(result["tests"]) > limit:
        lines.append(f"  ... +{len(result['tests']) - limit}")
    if result["untested"]:
        lines.append("no test reaches: " + ", ".join(result["untested"]))
    if result["not_indexed"]:
        lines.append("not in the index: " + ", ".join(result["not_indexed"]))
    if result["run_all"]:
        lines.append("RUN ALL TESTS -- " + "; ".join(result["run_all"]))
    for cmd in result["commands"]:
        lines.append(f"run: {cmd}")
    return "\n".join(lines)


def render_overview(summary: dict) -> str:
    lines = [
        f"{summary['file_count']} code files ({summary['tests']} tests), {summary['edge_count']} import edges",
        "most depended on: " + ", ".join(f"{x['file']} ({x['count']})" for x in summary["most_depended_on"]),
        "most dependencies: " + ", ".join(f"{x['file']} ({x['count']})" for x in summary["most_dependencies"]),
    ]
    if summary["cycles"]:
        lines.append(f"import cycles ({len(summary['cycles'])}): " + " ; ".join(" <-> ".join(c) for c in summary["cycles"][:5]))
    if summary["folder_edges"]:
        lines.append("folder layering: " + ", ".join(f"{x['from']} -> {x['to']} ({x['count']})" for x in summary["folder_edges"]))
    lines.append(f"never imported: {summary['never_imported_count']} file(s)")
    if summary["unresolved_local_imports"]:
        lines.append("unresolved relative imports: " + ", ".join(f"{n} ({c})" for n, c in summary["unresolved_local_imports"]))
    return "\n".join(lines)
