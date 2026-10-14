"""Score a pack's Python import edges against an independent ast-based ground
truth (no Ziplex code involved on the ground-truth side).

    python eval/edges_python.py <repo> <aif.json> [--roots src,.]

--roots lists sys.path roots relative to <repo> (default: "." plus "src"
when it exists). Prints precision/recall over .py -> .py edges and
classifies every miss: the source file was not packed at all, or the
resolver missed it. Exit code 0 always; this is a measurement, not a test.
"""
import argparse
import ast
import json
import os
import sys


def _python_files(repo: str) -> set[str]:
    out = set()
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in {".git", ".venv", ".claude", ".tox", ".mypy_cache", ".pytest_cache", "node_modules", "venv", "__pycache__", "build", "dist"}]
        for name in filenames:
            if name.endswith(".py"):
                out.add(os.path.relpath(os.path.join(dirpath, name), repo).replace("\\", "/"))
    return out


def ground_truth(repo: str, roots: list[str]) -> set[tuple[str, str]]:
    files = _python_files(repo)

    def module_file(dotted: str) -> str | None:
        path = dotted.replace(".", "/")
        for root in roots:
            base = f"{root}/{path}" if root not in ("", ".") else path
            for cand in (base + ".py", base + "/__init__.py"):
                if cand in files:
                    return cand
        return None

    def package_file(path: str) -> str | None:
        for cand in (path + ".py", path + "/__init__.py"):
            if cand in files:
                return cand
        return None

    edges = set()
    for src in sorted(files):
        try:
            tree = ast.parse(open(os.path.join(repo, src), encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = module_file(alias.name)
                    if target:
                        targets.add(target)
            elif isinstance(node, ast.ImportFrom) and node.level:
                parts = os.path.dirname(src).split("/") if os.path.dirname(src) else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = "/".join(p for p in parts + (node.module.split(".") if node.module else []) if p)
                for alias in node.names:
                    target = package_file(f"{base}/{alias.name}" if base else alias.name) or package_file(base)
                    if target:
                        targets.add(target)
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    target = module_file(f"{node.module}.{alias.name}") or module_file(node.module)
                    if target:
                        targets.add(target)
        targets.discard(src)
        edges.update((src, t) for t in targets)
    return edges


def packed_edges(aif_path: str) -> tuple[set[tuple[str, str]], set[str]]:
    aif = json.load(open(aif_path, encoding="utf-8"))
    edges = set()
    for src, rel in aif.get("relationships", {}).items():
        weak = set(rel.get("internal_text_refs", []))
        if src.endswith(".py"):
            edges.update((src, t) for t in rel.get("internal", []) if t.endswith(".py") and t not in weak)
    return edges, set(aif.get("files", {}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("aif")
    parser.add_argument("--roots", default=None)
    args = parser.parse_args()
    roots = args.roots.split(",") if args.roots else ["."] + (["src"] if os.path.isdir(os.path.join(args.repo, "src")) else [])

    truth = ground_truth(args.repo, roots)
    got, packed = packed_edges(args.aif)
    hit = truth & got
    missing = truth - got
    extra = got - truth
    print(f"ground truth {len(truth)}  packed {len(got)}  matched {len(hit)}")
    print(f"recall {len(hit) / max(len(truth), 1):.4f}  precision {len(hit) / max(len(got), 1):.4f}")
    not_packed = sorted(m for m in missing if m[0] not in packed)
    resolver = sorted(m for m in missing if m[0] in packed)
    print(f"missing: {len(not_packed)} from files not packed, {len(resolver)} resolver misses")
    for src, dst in resolver[:30]:
        print(f"  MISS {src} -> {dst}")
    for src, dst in sorted(extra)[:30]:
        print(f"  EXTRA {src} -> {dst}")


if __name__ == "__main__":
    sys.exit(main())
