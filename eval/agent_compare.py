"""F3/F5 agent comparison: headless Claude Code with vs. without the shipped
`ziplex-mcp` (v2) on structural questions whose answers come from ground truth
independent of Ziplex. See eval/README.md.

    python eval/agent_compare.py setup --t2-repo <t2 clone> --t2-work <t2 work dir> --out <dir> [--n-impact 5 --n-importers 5]
    python eval/agent_compare.py run --out <dir> --conditions C0,C1 --reps 2 [--model sonnet]
    python eval/agent_compare.py report --out <dir>

C0 = Claude Code with Read/Grep/Glob only. C1 = C0 + `ziplex-mcp --project <repo>`
(index pre-built, as `ziplex install claude` leaves it). Both get the same
neutral prompt; neither is told to prefer any tool.
"""
import argparse
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edges_python import ground_truth  # noqa: E402

PROMPT = """You are answering a question about the code repository in the current directory.

Question: {question}

Extra tools may be available in this session; use whatever helps. Do not modify any files.
When you are done, end your reply with exactly one line of the form
ANSWER: ["path/one.py", "path/two.py"]
listing repo-relative POSIX paths (an empty list if none)."""

QUESTIONS = {
    "impact": (
        "If `{target}` is changed, which test modules under the test suite could be affected -- "
        "i.e. which test files load it when pytest collects them, directly or indirectly "
        "(through other modules, package __init__.py files or conftest.py)? List the test module files."
    ),
    "importers": "Which files in this repository directly import `{target}`? List every such file.",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


# ---- setup -----------------------------------------------------------------

def setup(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t2_repo, t2_work = Path(args.t2_repo), Path(args.t2_work)
    sha = args.commit
    if not sha:  # the newest commit T2 evaluated
        head = json.loads((t2_work / "t2_results.json").read_text(encoding="utf-8"))["summary"]["head"]
        sha = next(p.stem for p in (t2_work / "gt").glob("*.json") if p.stem.startswith(head))
    gt = json.loads((t2_work / "gt" / f"{sha}.json").read_text(encoding="utf-8"))

    repo = out / "repo"
    if not repo.exists():
        _git(t2_repo, "worktree", "add", "--detach", str(repo), sha)
    runner = set(gt.get("runner") or [])
    universe = sorted(gt["universe"])
    loads = {t: set(gt["imports"].get(t, [])) for t in universe}
    rng = random.Random(args.seed)

    sources = sorted(
        f for f in set().union(*loads.values())
        if f.endswith(".py") and f not in universe and "conftest" not in f and f not in runner
    )
    impact = []
    for f in sources:
        tests = sorted(t for t in universe if f in loads[t])
        if 2 <= len(tests) <= max(3, int(0.5 * len(universe))):
            impact.append({"type": "impact", "target": f, "gold": tests})
    rng.shuffle(impact)

    roots = ["."] + (["src"] if (repo / "src").is_dir() else [])
    edges = ground_truth(str(repo), roots)
    importers: dict[str, set[str]] = {}
    for src, dst in edges:
        importers.setdefault(dst, set()).add(src)
    imp = [
        {"type": "importers", "target": dst, "gold": sorted(srcs)}
        for dst, srcs in sorted(importers.items())
        if 3 <= len(srcs) <= 15 and not dst.endswith("__init__.py")
    ]
    rng.shuffle(imp)

    questions = impact[: args.n_impact] + imp[: args.n_importers]
    for i, q in enumerate(questions):
        q["id"] = f"q{i:02d}"
        q["question"] = QUESTIONS[q["type"]].format(target=q["target"])
    (out / "questions.json").write_text(json.dumps({"commit": sha, "questions": questions}, indent=2), encoding="utf-8")

    # Pre-build the index the way `ziplex install claude` would.
    subprocess.run([sys.executable, "-m", "ziplex.cli", "index", str(repo)], check=True)
    mcp = {"mcpServers": {"ziplex": {"command": args.ziplex_mcp, "args": ["--project", str(repo.resolve())]}}}
    (out / "mcp.json").write_text(json.dumps(mcp, indent=2), encoding="utf-8")
    print(f"{len(questions)} questions at {sha[:10]} -> {out}")


# ---- run -------------------------------------------------------------------

def _parse_stream(lines: list[str]) -> dict:
    tools: dict[str, int] = {}
    result = {}
    text = ""
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tools[block["name"]] = tools.get(block["name"], 0) + 1
                elif block.get("type") == "text":
                    text = block.get("text", "")
        elif event.get("type") == "result":
            result = event
    usage = result.get("usage") or {}
    final = result.get("result") or text
    answer = None
    for line in reversed(final.splitlines()):
        if line.strip().startswith("ANSWER:"):
            try:
                answer = json.loads(line.split("ANSWER:", 1)[1].strip())
            except ValueError:
                answer = None
            break
    return {
        "tools": tools,
        "tool_calls": sum(tools.values()),
        "ziplex_calls": sum(n for k, n in tools.items() if k.startswith("mcp__ziplex")),
        "cost_usd": result.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_write": usage.get("cache_creation_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "turns": result.get("num_turns"),
        "duration_s": (result.get("duration_ms") or 0) / 1000,
        "is_error": result.get("is_error", True),
        "answer": answer,
    }


def _score(answer, gold) -> dict:
    if answer is None:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "answered": False}
    got = {str(a).replace("\\", "/").lstrip("./") for a in answer}
    gold = set(gold)
    hit = len(got & gold)
    p = hit / len(got) if got else (1.0 if not gold else 0.0)
    r = hit / len(gold) if gold else 1.0
    return {"precision": p, "recall": r, "f1": (2 * p * r / (p + r)) if p + r else 0.0, "answered": True}


def run(args) -> None:
    out = Path(args.out)
    spec = json.loads((out / "questions.json").read_text(encoding="utf-8"))
    repo = (out / "repo").resolve()
    results = out / "results.jsonl"
    done = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["id"], r["condition"], r["rep"]))
    conditions = args.conditions.split(",")
    jobs = [(q, c, rep) for rep in range(args.reps) for q in spec["questions"] for c in conditions]
    random.Random(args.seed).shuffle(jobs)  # interleave conditions against drift
    for q, condition, rep in jobs:
        if (q["id"], condition, rep) in done:
            continue
        cmd = [
            args.claude, "-p", PROMPT.format(question=q["question"]),
            "--output-format", "stream-json", "--verbose",
            "--model", args.model, "--restricted", "--tools", "Read,Grep,Glob",
            "--strict-mcp-config", "--no-session-persistence", "--max-budget-usd", str(args.max_budget),
        ]
        if condition == "C1":
            cmd += ["--mcp-config", str(out / "mcp.json"), "--allowedTools", "mcp__ziplex"]
        started = time.time()
        proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=args.timeout)
        row = {"id": q["id"], "type": q["type"], "target": q["target"], "condition": condition, "rep": rep,
               "wall_s": round(time.time() - started, 1), **_parse_stream(proc.stdout.splitlines())}
        row.update(_score(row["answer"], q["gold"]))
        if proc.returncode and not row["answered"]:
            row["stderr"] = proc.stderr[-500:]
        with results.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{q['id']} {condition} r{rep}: f1={row['f1']:.2f} calls={row['tool_calls']} "
              f"zx={row['ziplex_calls']} ${row['cost_usd']} {row['wall_s']}s", flush=True)


# ---- report ----------------------------------------------------------------

def _bootstrap_ratio(pairs: list[tuple[float, float]], n: int = 10000, seed: int = 0) -> tuple[float, float, float]:
    """Median of per-question C1/C0 ratios with a 95% bootstrap CI."""
    ratios = [b / a for a, b in pairs if a]
    if not ratios:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(n))
    return statistics.median(ratios), meds[int(0.025 * n)], meds[int(0.975 * n)]


def report(args) -> None:
    out = Path(args.out)
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    by = {}
    for r in rows:
        by.setdefault((r["id"], r["condition"]), []).append(r)
    ids = sorted({r["id"] for r in rows})
    metrics = ["f1", "cost_usd", "tool_calls", "duration_s", "output_tokens"]
    lines = [f"runs: {len(rows)}"]
    for cond in sorted({r["condition"] for r in rows}):
        cr = [r for r in rows if r["condition"] == cond]
        lines.append(
            f"{cond}: mean F1 {statistics.mean(r['f1'] for r in cr):.3f} | median cost ${statistics.median(r['cost_usd'] or 0 for r in cr):.3f}"
            f" | median calls {statistics.median(r['tool_calls'] for r in cr)} | median time {statistics.median(r['duration_s'] for r in cr):.1f}s"
            + (f" | used ziplex in {sum(1 for r in cr if r['ziplex_calls'])}/{len(cr)}" if cond != "C0" else "")
        )
    for m in metrics:
        pairs = []
        for i in ids:
            a, b = by.get((i, "C0")), by.get((i, "C1"))
            if a and b:
                pairs.append((statistics.median(x[m] or 0 for x in a), statistics.median(x[m] or 0 for x in b)))
        if m == "f1":
            diffs = [b - a for a, b in pairs]
            lines.append(f"F1 C1-C0 per question: mean {statistics.mean(diffs):+.3f}, worst {min(diffs):+.3f}")
        else:
            med, lo, hi = _bootstrap_ratio(pairs)
            lines.append(f"{m}: median C1/C0 = {med:.2f} (95% CI {lo:.2f}-{hi:.2f}) over {len(pairs)} questions")
    for t in sorted({r["type"] for r in rows}):
        for cond in sorted({r["condition"] for r in rows}):
            tr = [r for r in rows if r["type"] == t and r["condition"] == cond]
            if tr:
                lines.append(f"  {t}/{cond}: F1 {statistics.mean(r['f1'] for r in tr):.3f}, "
                             f"recall {statistics.mean(r['recall'] for r in tr):.3f}, cost ${statistics.mean(r['cost_usd'] or 0 for r in tr):.3f}")
    text = "\n".join(lines)
    (out / "report.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--t2-repo", required=True)
    s.add_argument("--t2-work", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--commit", default=None)
    s.add_argument("--n-impact", type=int, default=5)
    s.add_argument("--n-importers", type=int, default=5)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--ziplex-mcp", default="ziplex-mcp")
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--conditions", default="C0,C1")
    r.add_argument("--reps", type=int, default=2)
    r.add_argument("--model", default="sonnet")
    r.add_argument("--claude", default="claude")
    r.add_argument("--max-budget", type=float, default=2.0)
    r.add_argument("--timeout", type=int, default=900)
    r.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("report")
    p.add_argument("--out", required=True)
    args = parser.parse_args()
    {"setup": setup, "run": run, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
