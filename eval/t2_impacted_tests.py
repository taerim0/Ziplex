"""T2: score `Graph.impacted_tests()` on real commits against an import-trace
ground truth (docs/REVIEW-2026-10-14.md §7, falsification row F2: recall
>= 0.98, median selection ratio <= 0.6).

    python eval/t2_impacted_tests.py --repo <clone> --python <venv python> \
        --work <scratch dir> [--commits 25] [--tests-dir tests] [--jobs 8]

<clone> is a git clone with history (`git clone --filter=blob:none` is
enough); the script checks out each commit in it (detached, --force), so
use a dedicated clone. <venv python> is an interpreter that has the repo
installed in editable mode plus pytest and the test dependencies.

Commits: the last --commits non-merge commits that change a non-test .py
file outside tests/docs/examples/benchmarks/tools/scripts. Each is processed
oldest first; the query is that commit's added/modified non-test .py files
(test files are left out on both sides -- a changed test trivially selects
itself), against the tree at that commit.

Ground truth (independent of Ziplex): pytest --collect-only over
--tests-dir gives the universe of test modules (files holding
pytest.Function items; a module that fails to collect on its own is
reported as broken and left out); then, per test module, a
separate `pytest --collect-only -p t2_tracer <test>` records every repo .py
file in sys.modules after collection (the module, the conftest.py files
pytest loaded for it, and all they import). A test is truly impacted when
that set contains a changed file. Results are cached per commit under
<work>/gt/<sha>.json (they depend only on the tree).

Ziplex side: one GraphIndex at <work>/index.db, updated incrementally per
commit (the first update is the full build); `impacted_tests(files=...)`.

Every recall miss is classified (see `classify_miss`) using an ast-based
static import graph from eval/edges_python.py, so the class does not depend
on Ziplex's resolver either.

Counterfactual (cf_*): the same selection done over an ast-only graph that
keeps only imports executed at import time (no function bodies, no
`if TYPE_CHECKING:` / `if __name__ == "__main__":` blocks) and adds
module -> enclosing package __init__.py edges. It shows how much of the
gap to the oracle a static selector could close. cfk_* is the same but keeps
function-local imports (they don't run at collection, but do once a test
calls the function, so dropping them trades runtime recall). Writes <work>/t2_results.json and prints a
per-commit table plus the aggregate.
"""
import argparse
import ast
import json
import os
import posixpath
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from edges_python import _python_files, ground_truth as ast_edges  # noqa: E402

_NON_SOURCE_DIRS = {"tests", "test", "testing", "docs", "doc", "examples", "benchmarks", "tools", "scripts"}


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout


def is_source_py(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    parts = path.split("/")
    name = parts[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return False
    return not any(p in _NON_SOURCE_DIRS for p in parts[:-1])


def pick_commits(repo: str, count: int, ref: str) -> list[dict]:
    """Newest-first scan from `ref`; returns oldest first."""
    out = []
    log = git(repo, "log", "--no-merges", "--format=@%H", "--name-status", "-n", "600", ref)
    current = None
    for line in log.splitlines():
        if line.startswith("@"):
            current = {"sha": line[1:], "changes": []}
            out.append(current)
        elif line.strip() and current is not None:
            status, *paths = line.split("\t")
            current["changes"].append((status[0], paths[-1]))
    picked = [c for c in out if any(s != "D" and is_source_py(p) for s, p in c["changes"])][:count]
    return list(reversed(picked))


# ---- ground truth ------------------------------------------------------------

def _pytest_env(repo: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["T2_TRACE_ROOT"] = repo
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(key, None)
    return env


def collect_universe(repo: str, python: str, tests_dir: str, out_path: str) -> list[str]:
    """Test modules of the whole suite: files holding pytest.Function items
    (the tracer reports them), so conftest-collected data files don't count."""
    data = trace_test(repo, python, tests_dir, out_path)
    modules = data.get("test_modules") or []
    if not modules:
        raise RuntimeError(f"no tests collected in {repo}: {' | '.join(data['errors'])[-1500:]}")
    return modules


def trace_test(repo: str, python: str, test: str, out_path: str) -> dict:
    env = _pytest_env(repo)
    env["T2_TRACE_OUT"] = out_path
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = subprocess.run(
        [python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "t2_tracer", test],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if not os.path.exists(out_path):
        return {"files": [], "errors": [f"no trace (exit {proc.returncode}): {proc.stdout[-600:]} {proc.stderr[-600:]}"]}
    with open(out_path, encoding="utf-8") as f:
        data = json.load(f)
    os.remove(out_path)
    return data


def runner_baseline(repo: str, python: str, tmp: Path) -> list[str]:
    """Repo files pytest itself (plus installed plugins) imports for an empty
    test outside the repo -- e.g. pytest's terminal writer imports pygments'
    lexers, so pygments' suite depends on pygments through its own runner."""
    empty = tmp / "_empty"
    empty.mkdir(parents=True, exist_ok=True)
    (empty / "test_t2_empty.py").write_text("def test_empty():\n    pass\n", encoding="utf-8")
    (empty / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    env = _pytest_env(repo)
    env["T2_TRACE_OUT"] = str(empty / "out.json")
    subprocess.run(
        [python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "t2_tracer",
         "-c", str(empty / "pytest.ini"), "--rootdir", str(empty), str(empty / "test_t2_empty.py")],
        cwd=str(empty), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    with open(empty / "out.json", encoding="utf-8") as f:
        return json.load(f)["files"]


def ground_truth(repo: str, python: str, tests_dir: str, sha: str, work: Path, jobs: int) -> dict:
    cache = work / "gt" / f"{sha}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if "runner" not in data:  # cache written before the baseline existed
            data["runner"] = runner_baseline(repo, python, work / "trace_tmp")
            cache.write_text(json.dumps(data), encoding="utf-8")
        return data
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = work / "trace_tmp"
    tmp.mkdir(exist_ok=True)
    universe = collect_universe(repo, python, tests_dir, str(tmp / "_universe.json"))
    with ThreadPoolExecutor(jobs) as pool:
        traces = list(pool.map(
            lambda t: trace_test(repo, python, t, str(tmp / (t.replace("/", "__") + ".json"))), universe,
        ))
    data = {
        "universe": universe,
        "imports": {t: tr["files"] for t, tr in zip(universe, traces)},
        "errors": {t: tr["errors"] for t, tr in zip(universe, traces) if tr["errors"]},
        "runner": runner_baseline(repo, python, tmp),
    }
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


# ---- miss classification (ast graph, not Ziplex) -----------------------------

def _closure(start: str, out: dict[str, set[str]]) -> set[str]:
    seen, stack = {start}, [start]
    while stack:
        for dst in out.get(stack.pop(), ()):
            if dst not in seen:
                seen.add(dst)
                stack.append(dst)
    return seen


def classify_miss(test: str, changed_hit: list[str], traced: set[str], ast_out: dict[str, set[str]],
                  ziplex_closure: set[str], runner: set[str]) -> str:
    """Why a truly impacted test was not selected, judged per changed file the
    test actually loaded (the most fixable class wins):
    - static-edge: the ast graph (plus conftest chain) reaches the changed
      file, so a static resolver could have -- a Ziplex resolver miss;
    - parent-package: the changed file is a package __init__.py whose
      package contains a module the test statically reaches (Python runs
      every parent __init__ on `import a.b.c`) -- fixable statically;
    - conftest-chain: reached only through a conftest.py the test loaded
      that the ast graph does not tie to the test (e.g. a rootdir conftest
      outside the test's folder chain, pytest_plugins);
    - dynamic: none of the above -- importlib/__import__, a lazy
      module __getattr__, entry-point plugins, imports executed by
      collection-time code paths;
    - runner: pytest itself imports the changed file even for an empty test
      outside the repo (`runner_baseline`) -- no test-to-code graph can see it."""
    reach = set()
    folder = posixpath.dirname(test)
    chain = [test]
    while True:
        conftest = posixpath.join(folder, "conftest.py") if folder else "conftest.py"
        chain.append(conftest)
        if not folder:
            break
        folder = posixpath.dirname(folder)
    for start in chain:
        if start in traced or start == test:
            reach |= _closure(start, ast_out)
    classes = []
    for changed in changed_hit:
        if changed in reach:
            classes.append("static-edge" if changed not in ziplex_closure else "ziplex-reaches-but-unselected")
        elif changed.endswith("__init__.py") and any(
            r != changed and r.startswith(posixpath.dirname(changed) + "/") for r in reach
        ):
            classes.append("parent-package")
        elif changed in runner:
            classes.append("runner")
        elif any(c.endswith("conftest.py") for c in traced - set(chain)):
            classes.append("conftest-chain")
        else:
            classes.append("dynamic")
    order = ["static-edge", "ziplex-reaches-but-unselected", "parent-package", "conftest-chain", "dynamic", "runner"]
    return min(classes, key=order.index)


# ---- counterfactual: a static selector that models import-time execution ----

def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _is_main_guard(test: ast.expr) -> bool:
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators))


def _import_time_nodes(tree: ast.Module, skip_functions: bool = True):
    """Import statements that run when the module is imported: skips function
    bodies (unless skip_functions=False), `if TYPE_CHECKING:` and
    `if __name__ == "__main__":` blocks (their `else` still runs); keeps
    class bodies and both try/except arms."""
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if skip_functions and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.If) and (_is_type_checking(node.test) or _is_main_guard(node.test)):
            stack.extend(node.orelse)
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
            continue
        stack.extend(ast.iter_child_nodes(node))


def import_time_graph(repo: str, roots: list[str], skip_functions: bool = True) -> dict[str, set[str]]:
    """ast-only graph of what importing each .py file executes: import-time
    imports (resolved like eval/edges_python.py) plus an edge from every module
    to each __init__.py of its enclosing packages, since Python runs those
    first. No Ziplex code."""
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

    out: dict[str, set[str]] = {}
    for src in sorted(files):
        targets = set()
        folder = posixpath.dirname(src)
        while folder:
            init = folder + "/__init__.py"
            if init in files:
                targets.add(init)
            else:
                break
            folder = posixpath.dirname(folder)
        try:
            with open(os.path.join(repo, src), encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError, ValueError):
            out[src] = targets - {src}
            continue
        for node in _import_time_nodes(tree, skip_functions):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    for i in range(1, len(parts) + 1):  # import a.b.c runs a, a.b, a.b.c
                        target = module_file(".".join(parts[:i]))
                        if target:
                            targets.add(target)
            elif node.level:
                parts = src.split("/")[:-1]
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = "/".join(p for p in parts + (node.module.split(".") if node.module else []) if p)
                if package_file(base):
                    targets.add(package_file(base))
                for alias in node.names:
                    target = package_file(f"{base}/{alias.name}" if base else alias.name)
                    if target:
                        targets.add(target)
            elif node.module:
                parts = node.module.split(".")
                for i in range(1, len(parts) + 1):
                    target = module_file(".".join(parts[:i]))
                    if target:
                        targets.add(target)
                for alias in node.names:
                    target = module_file(f"{node.module}.{alias.name}")
                    if target:
                        targets.add(target)
        out[src] = targets - {src}
    return out


def conftest_chain(test: str, files: set[str]) -> list[str]:
    chain, folder = [], posixpath.dirname(test)
    while True:
        conftest = posixpath.join(folder, "conftest.py") if folder else "conftest.py"
        if conftest in files:
            chain.append(conftest)
        if not folder:
            break
        folder = posixpath.dirname(folder)
    return chain


def ziplex_forward_closure(graph, test: str) -> set[str]:
    g = graph._graph()
    seen, stack = {test}, [test]
    while stack:
        for dst, _, kind in g.out.get(stack.pop(), []):
            if kind != "text" and dst not in seen:
                seen.add(dst)
                stack.append(dst)
    return seen


# ---- main --------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--commits", type=int, default=25)
    parser.add_argument("--tests-dir", default="tests")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--ref", default="origin/HEAD",
                        help="branch tip to pick commits from (not HEAD: a previous run leaves the clone detached)")
    parser.add_argument("--roots", default=None, help="sys.path roots for the ast miss classifier (default: . + src)")
    args = parser.parse_args()

    repo = os.path.realpath(args.repo)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE.parent / "src"))
    from ziplex.core.api import Graph

    roots = args.roots.split(",") if args.roots else ["."] + (["src"] if os.path.isdir(os.path.join(repo, "src")) else [])
    commits = pick_commits(repo, args.commits, args.ref)
    graph = None
    rows, skipped = [], []
    for i, commit in enumerate(commits):
        sha = commit["sha"]
        git(repo, "checkout", "-q", "--force", sha)
        changed_src = sorted({p for s, p in commit["changes"] if s != "D" and is_source_py(p)})
        changed_all = sorted({p for s, p in commit["changes"] if s != "D"})

        t0 = time.perf_counter()
        if graph is None:
            graph = Graph(repo, db_path=str(work / "index.db"))
        stats = graph.refresh()
        t_update = time.perf_counter() - t0
        t0 = time.perf_counter()
        result = graph.impacted_tests(files=changed_src)
        t_query = time.perf_counter() - t0
        run_all = graph.impacted_tests(files=changed_all)["run_all"]

        t0 = time.perf_counter()
        try:
            gt = ground_truth(repo, args.python, args.tests_dir, sha, work, args.jobs)
        except RuntimeError as exc:  # the suite itself doesn't collect at this commit
            skipped.append({"sha": sha[:9], "reason": str(exc).splitlines()[-1][:200]})
            print(f"[{i + 1}/{len(commits)}] {sha[:9]} SKIPPED: {skipped[-1]['reason']}", flush=True)
            continue
        t_gt = time.perf_counter() - t0
        universe = set(gt["universe"])
        broken = set(gt["errors"])
        scored = universe - broken
        changed_set = set(changed_src)
        truly = {t for t in scored if changed_set & set(gt["imports"][t])}
        selected_all = {t["file"] for t in result["tests"]}
        selected = selected_all & scored
        hit = truly & selected
        # the same, ignoring changed files pytest itself imports (runner class)
        runner = set(gt["runner"])
        truly_nr = {t for t in scored if (changed_set - runner) & set(gt["imports"][t])}

        def static_select(out: dict[str, set[str]]) -> set[str]:
            files = set(out)
            chosen = set()
            for t in scored:
                reach = set()
                for start in [t] + conftest_chain(t, files):
                    reach |= _closure(start, out)
                if reach & changed_set:
                    chosen.add(t)
            return chosen

        cf_selected = static_select(import_time_graph(repo, roots))
        cf_hit = cf_selected & truly
        # keeps function-local imports: they don't run at collection but do
        # when a test calls the function, so dropping them is not free
        cfk_selected = static_select(import_time_graph(repo, roots, skip_functions=False))
        cfk_hit = cfk_selected & truly

        misses = []
        if truly - selected:
            ast_out: dict[str, set[str]] = {}
            for src, dst in ast_edges(repo, roots):
                ast_out.setdefault(src, set()).add(dst)
            for t in sorted(truly - selected):
                traced = set(gt["imports"][t])
                hit_files = sorted(changed_set & traced)
                misses.append({
                    "test": t, "changed": hit_files,
                    "class": classify_miss(t, hit_files, traced, ast_out, ziplex_forward_closure(graph, t), runner),
                })
        row = {
            "sha": sha[:9], "changed": changed_src, "n_changed": len(changed_src),
            "universe": len(scored), "broken": sorted(broken),
            "truly": len(truly), "selected": len(selected), "hit": len(hit),
            "selected_outside_universe": sorted(selected_all - universe),
            "recall": len(hit) / len(truly) if truly else None,
            "precision": len(hit) / len(selected) if selected else None,
            "ratio": len(selected) / len(scored) if scored else None,
            "oracle_ratio": len(truly) / len(scored) if scored else None,
            "truly_nr": len(truly_nr), "hit_nr": len(truly_nr & selected),
            "runner_changed": sorted(changed_set & runner),
            "cf_selected": len(cf_selected),
            "cf_recall": len(cf_hit) / len(truly) if truly else None,
            "cf_ratio": len(cf_selected) / len(scored) if scored else None,
            "cf_missed": sorted(truly - cf_selected),
            "cfk_recall": len(cfk_hit) / len(truly) if truly else None,
            "cfk_ratio": len(cfk_selected) / len(scored) if scored else None,
            "run_all": run_all, "not_indexed": result["not_indexed"],
            "update_s": round(t_update, 3), "query_s": round(t_query, 4), "gt_s": round(t_gt, 1),
            "full_resolve": stats["full_resolve"], "misses": misses,
        }
        rows.append(row)
        rec = "  -  " if row["recall"] is None else f"{row['recall']:.3f}"
        prec = "  -  " if row["precision"] is None else f"{row['precision']:.3f}"
        print(f"[{i + 1}/{len(commits)}] {sha[:9]} chg={len(changed_src)} truly={len(truly)} sel={len(selected)} "
              f"recall={rec} prec={prec} ratio={row['ratio']:.3f} oracle={row['oracle_ratio']:.3f} "
              f"upd={t_update:.2f}s q={t_query * 1000:.1f}ms cf_sel={len(cf_selected)}"
              + (f" cf_missed={len(truly - cf_selected)}" if truly - cf_selected else "")
              + (" RUN_ALL" if run_all else "")
              + (f" broken={len(broken)}" if broken else ""), flush=True)
        for m in misses:
            print(f"    MISS {m['test']} <- {', '.join(m['changed'])} [{m['class']}]", flush=True)

    recalls = [r["recall"] for r in rows if r["recall"] is not None]
    precisions = [r["precision"] for r in rows if r["precision"] is not None]
    ratios = [r["ratio"] for r in rows if r["ratio"] is not None]
    oracle = [r["oracle_ratio"] for r in rows if r["oracle_ratio"] is not None]
    total_truly = sum(r["truly"] for r in rows)
    total_hit = sum(r["hit"] for r in rows)
    miss_classes: dict[str, int] = {}
    for r in rows:
        for m in r["misses"]:
            miss_classes[m["class"]] = miss_classes.get(m["class"], 0) + 1
    summary = {
        "repo": os.path.basename(repo), "head": git(repo, "rev-parse", "--short", commits[-1]["sha"]).strip() if commits else None,
        "commits": len(rows), "skipped": skipped, "commits_with_impacted": len(recalls),
        "pooled_recall": total_hit / total_truly if total_truly else None,
        "pooled_recall_excl_runner": (sum(r["hit_nr"] for r in rows) / sum(r["truly_nr"] for r in rows))
        if sum(r["truly_nr"] for r in rows) else None,
        "median_recall": statistics.median(recalls) if recalls else None,
        "mean_recall": statistics.mean(recalls) if recalls else None,
        "min_recall": min(recalls) if recalls else None,
        "median_precision": statistics.median(precisions) if precisions else None,
        "median_ratio": statistics.median(ratios) if ratios else None,
        "mean_ratio": statistics.mean(ratios) if ratios else None,
        "median_oracle_ratio": statistics.median(oracle) if oracle else None,
        "cf_pooled_recall": (sum(r["cf_recall"] * r["truly"] for r in rows if r["truly"]) / total_truly) if total_truly else None,
        "cf_median_ratio": statistics.median(r["cf_ratio"] for r in rows if r["cf_ratio"] is not None) if rows else None,
        "cfk_pooled_recall": (sum(r["cfk_recall"] * r["truly"] for r in rows if r["truly"]) / total_truly) if total_truly else None,
        "cfk_median_ratio": statistics.median(r["cfk_ratio"] for r in rows if r["cfk_ratio"] is not None) if rows else None,
        "run_all_commits": sum(1 for r in rows if r["run_all"]),
        "miss_classes": miss_classes,
        "first_update_s": rows[0]["update_s"] if rows else None,
        "median_update_s": statistics.median(r["update_s"] for r in rows[1:]) if len(rows) > 1 else None,
        "median_query_ms": statistics.median(r["query_s"] for r in rows) * 1000 if rows else None,
    }
    (work / "t2_results.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
