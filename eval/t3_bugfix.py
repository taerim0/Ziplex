"""T3: real bug fixes graded by hidden tests -- headless Claude Code alone (C0)
vs. + the shipped `ziplex-mcp` (C1) vs. C1 + the PostToolUse hook (C2).
See eval/README.md and docs/REVIEW-2026-10-14.md §7.

    python eval/t3_bugfix.py mine     --work <dir> --repos-root <clones> [--repos click,flask,rich,pygments]
    python eval/t3_bugfix.py validate --work <dir> --repos-root <clones> --venvs-root <venvs> [--target 12]
    python eval/t3_bugfix.py run      --work <dir> --repos-root <clones> --venvs-root <venvs> \
                                      --conditions C0,C1,C2 --reps 1 [--tasks id,id] [--model sonnet]
    python eval/t3_bugfix.py report   --work <dir>

<clones>/<repo> are git clones with history (blobless is enough); they are
never checked out in place -- every step uses `git worktree add --detach`
under <work>/wt. <venvs>/<repo> has the package installed editable plus
pytest; PYTHONPATH points at the worktree (its src/ when present) so tests
import the worktree's code, not the clone the editable install points at.
"""
import argparse
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PROMPT = """{task}

Fix the bug in this repository. You can run the test suite with `{python} -m pytest ...`.
Extra tools may be available; use whatever helps. Do not commit."""

FIX_RE = re.compile(r"\b(fix|fixes|fixed|fixing|bug|bugs|regression|crash|crashes|incorrect|incorrectly|wrong|broken)\b",
                    re.I)
# Feature-shaped subjects; kept only when the subject itself also names a fix.
FEATURE_SUBJECT_RE = re.compile(r"^\s*(add|adds|deprecate|implement|support|update|base|allow|introduce|improve)\b", re.I)
SKIP_SUBJECT_RE = re.compile(r"^\s*(docs?|typing|types|ci|chore|bump|release|build|style|lint|refactor|test|tests)\b[:(\s]",
                             re.I)
# Lines describing the code change rather than the symptom.
CHANGE_LINE_RE = re.compile(
    r"^\s*[-*]?\s*(use|uses|replace|replaces|change|changes|changed|switch|switches|move|moves|rename|renames|"
    r"refactor|call|calls|pass|passes|return|returns|make|makes|add|adds|added|remove|removes|removed|"
    r"update|updates|this (pr|commit|change)|instead)\b|instead of|co-authored-by|signed-off-by|^\s*\*\s*$", re.I)
ISSUE_REF_RE = re.compile(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s*:?\s+(?:[\w.-]+/[\w.-]+)?#(\d+)", re.I)
PR_REF_RE = re.compile(r"\(#(\d+)\)\s*$")
TEST_DIRS = {"tests", "test", "testing"}
NON_SOURCE_DIRS = TEST_DIRS | {"docs", "doc", "examples", "benchmarks", "tools", "scripts", "artwork", "external"}


def git(repo, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check,
                          encoding="utf-8", errors="replace").stdout


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return any(p in TEST_DIRS for p in parts[:-1]) or name.startswith("test_") or name.endswith("_test.py") \
        or name == "conftest.py"


def is_source_py(path: str) -> bool:
    if not path.endswith(".py") or is_test_path(path):
        return False
    return not any(p in NON_SOURCE_DIRS for p in path.split("/")[:-1]) and path.split("/")[-1] != "setup.py"


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---- mine ------------------------------------------------------------------

def _clean_task_text(subject: str, body: str) -> str:
    subject = PR_REF_RE.sub("", subject).strip()
    keep = []
    for line in body.splitlines():
        if CHANGE_LINE_RE.search(line) or ISSUE_REF_RE.search(line) or line.strip().startswith("```"):
            continue
        keep.append(line.rstrip())
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()
    return subject + ("\n\n" + body if body else "")


def _clean_issue_body(body: str) -> str:
    body = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    body = re.sub(r"```diff.*?```", "", body, flags=re.S)  # proposed patches leak the fix
    body = re.sub(r"\r\n?", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body[:3000]


def _gh(endpoint: str) -> dict | None:
    proc = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def _issue_for(slug: str, sha: str, subject: str, body: str) -> dict | None:
    """The issue linked from the commit or its PR, as {kind, number, title, body}."""
    refs = ISSUE_REF_RE.findall(subject + "\n" + body)
    if not refs:
        pr_num = PR_REF_RE.search(subject)
        prs = [_gh(f"repos/{slug}/pulls/{pr_num.group(1)}")] if pr_num else (_gh(f"repos/{slug}/commits/{sha}/pulls") or [])
        for pr in prs:
            if pr:
                refs += ISSUE_REF_RE.findall((pr.get("title") or "") + "\n" + (pr.get("body") or ""))
    for num in refs:
        issue = _gh(f"repos/{slug}/issues/{num}")
        if issue and "pull_request" not in issue:
            return {"kind": "issue", "number": int(num), "title": issue["title"],
                    "body": _clean_issue_body(issue.get("body"))}
    return None


def mine(args) -> None:
    work = Path(args.work)
    out = []
    for repo_name in args.repos.split(","):
        repo = Path(args.repos_root) / repo_name
        slug = re.sub(r"(\.git)?/?$", "", git(repo, "remote", "get-url", "origin").strip()).split("github.com/")[-1]
        # name-status only needs trees (cheap on a blobless clone); numstat fetches blobs, so it runs on survivors.
        log = git(repo, "log", "--no-merges", "-n", str(args.commits), "--format=%x00%H%x1f%P%x1f%s%x1f%b%x1e",
                  "--name-status", args.ref)
        n_scanned, seen_subjects = 0, set()
        for chunk in log.split("\x00")[1:]:
            n_scanned += 1
            head, _, files_part = chunk.partition("\x1e")
            sha, parents, subject, body = head.split("\x1f")
            if len(parents.split()) != 1 or SKIP_SUBJECT_RE.search(subject):
                continue
            if not FIX_RE.search(subject + "\n" + body) or (FEATURE_SUBJECT_RE.search(subject)
                                                            and not FIX_RE.search(subject)):
                continue
            if PR_REF_RE.sub("", subject).strip().lower() in seen_subjects:  # cherry-picks onto other branches
                continue
            changes = []
            for line in files_part.strip().splitlines():
                status, *paths = line.split("\t")
                changes.append((status[0], paths[-1]))
            src = [p for s, p in changes if is_source_py(p)]
            tests = [(s, p) for s, p in changes if is_test_path(p)]
            other = [p for s, p in changes if p not in src and not is_test_path(p)]
            if not 1 <= len(src) <= 3 or not any(s != "D" and not p.endswith("conftest.py") for s, p in tests):
                continue
            if any(s not in "MA" for s, p in changes if p in src) or any(s == "R" for s, _ in tests):
                continue
            if any(o.endswith(".py") for o in other):  # .py outside src/tests (examples, setup.py): not a clean fix
                continue
            num = git(repo, "show", "--numstat", "--format=", sha, "--", *src)
            changed = sum(int(a) + int(b) for a, b, _ in (l.split("\t") for l in num.splitlines() if l.strip())
                          if a != "-")
            if changed > args.max_lines:
                continue
            seen_subjects.add(PR_REF_RE.sub("", subject).strip().lower())
            out.append({"id": f"{repo_name}-{sha[:8]}", "repo": repo_name, "slug": slug, "sha": sha,
                        "parent": parents.strip(), "subject": subject, "body": body.strip(),
                        "source_files": src, "test_changes": tests, "other_files": other,
                        "source_lines": changed})
        print(f"{repo_name}: scanned {n_scanned}, candidates {sum(1 for c in out if c['repo'] == repo_name)}")
    for c in out:
        issue = _issue_for(c["slug"], c["sha"], c["subject"], c["body"]) if not args.no_gh else None
        c["issue"] = issue
        if issue:
            c["task"] = f"{issue['title']}\n\n{issue['body']}".strip()
            c["task_source"] = f"issue #{issue['number']}"
        else:
            c["task"] = _clean_task_text(c["subject"], c["body"])
            c["task_source"] = "commit message"
    _dump(work / "candidates.json", out)
    print(f"{len(out)} candidates -> {work / 'candidates.json'}")


# ---- worktrees / pytest ----------------------------------------------------

def add_worktree(repo: Path, path: Path, commit: str) -> None:
    remove_worktree(repo, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "--detach", "--force", str(path), commit)


def remove_worktree(repo: Path, path: Path) -> None:
    if path.exists():
        git(repo, "worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)


def test_env(wt: Path) -> dict:
    env = dict(os.environ)
    root = wt / "src" if (wt / "src").is_dir() else wt
    env["PYTHONPATH"] = str(root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in ("PYTEST_PLUGINS", "VIRTUAL_ENV"):
        env.pop(key, None)
    # Older commits + today's pytest under `filterwarnings = error`: a pytest
    # deprecation would abort collection. Applies to the agent's runs too.
    env["PYTEST_ADDOPTS"] = "-W ignore::pytest.PytestRemovedIn10Warning"
    return env


def apply_tests(repo: Path, wt: Path, task: dict) -> None:
    for status, path in task["test_changes"]:
        if status == "D":
            (wt / path).unlink(missing_ok=True)
        else:
            git(wt, "checkout", task["sha"], "--", path)


def pytest(python: str, wt: Path, targets: list[str], junit: Path, timeout: int) -> dict:
    """rc, wall time and the failing node ids (from a junit xml, xunit1 for the file attr)."""
    junit.unlink(missing_ok=True)
    started = time.time()
    try:
        proc = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "junit_family=xunit1",
                               f"--junitxml={junit}", *targets], cwd=wt, env=test_env(wt), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        rc, tail = proc.returncode, proc.stdout[-1500:]
    except subprocess.TimeoutExpired:
        rc, tail = -1, "timeout"
    failing, n = [], 0
    if junit.exists():
        try:
            for case in ET.parse(junit).getroot().iter("testcase"):
                n += 1
                if case.find("failure") is not None or case.find("error") is not None:
                    failing.append({"id": f"{case.get('classname')}::{case.get('name')}",
                                    "file": (case.get("file") or "").replace("\\", "/")})
        except ET.ParseError:
            pass
    return {"rc": rc, "seconds": round(time.time() - started, 1), "n": n, "failing": failing, "tail": tail}


def _targets(task: dict) -> list[str]:
    return [p for s, p in task["test_changes"] if s != "D" and not p.endswith("conftest.py")]


def _python(args, repo_name: str) -> str:
    return str(Path(args.venvs_root) / repo_name / "Scripts" / "python.exe")


# ---- validate ----------------------------------------------------------------

def validate(args) -> None:
    work = Path(args.work)
    cands = _load(work / "candidates.json", [])
    state = _load(work / "validation.json", {})
    for item in args.reject or []:  # manual review: hidden tests that pin details the task text can't convey
        tid, _, why = item.partition(":")
        state[tid] = {"id": tid, "reason": f"rejected on review: {why or 'overspecified hidden tests'}"}
    by_repo: dict[str, list[dict]] = {}
    for c in cands:
        by_repo.setdefault(c["repo"], []).append(c)
    for lst in by_repo.values():  # issue-framed first, terse one-liners last, newest first within each
        lst.sort(key=lambda c: (c["issue"] is None, len(c["task"]) < 60))
    order = []  # round-robin across repos
    while any(by_repo.values()):
        for r in list(by_repo):
            if by_repo[r]:
                order.append(by_repo[r].pop(0))
    def selected() -> list[dict]:
        """Valid tasks in validation order, fast suites only, at most --max-per-repo per repo."""
        out, per_repo = [], {}
        for c in order:
            v = state.get(c["id"], {})
            if v.get("valid") and v["full"]["seconds"] <= args.max_suite_seconds                     and per_repo.get(c["repo"], 0) < args.max_per_repo:
                per_repo[c["repo"]] = per_repo.get(c["repo"], 0) + 1
                out.append(c)
        return out[: args.target]

    for task in order:
        if len(selected()) >= args.target:
            break
        if task["id"] in state:
            continue
        if sum(1 for c in selected() if c["repo"] == task["repo"]) >= args.max_per_repo:
            continue
        repo, python = Path(args.repos_root) / task["repo"], _python(args, task["repo"])
        wt = work / "wt" / f"val-{task['id']}"
        rec: dict = {"id": task["id"]}
        try:
            add_worktree(repo, wt, task["parent"])
            apply_tests(repo, wt, task)
            targets = _targets(task)
            before = pytest(python, wt, targets, work / "junit" / f"{task['id']}-before.xml", args.timeout)
            rec["before"] = {k: before[k] for k in ("rc", "n", "failing")}
            if before["rc"] != 1 or not before["failing"]:
                rec["reason"] = f"hidden tests don't fail cleanly before the fix (rc={before['rc']})"
                rec["tail"] = before["tail"][-600:]
            else:
                git(wt, "checkout", task["sha"], "--", *task["source_files"])
                after = pytest(python, wt, targets, work / "junit" / f"{task['id']}-after.xml", args.timeout)
                rec["after"] = {k: after[k] for k in ("rc", "n", "failing")}
                if after["rc"] != 0:
                    rec["reason"] = f"hidden tests don't pass with the fix (rc={after['rc']})"
                    rec["tail"] = after["tail"][-600:]
                else:
                    # Baseline full suite: parent + hidden tests, no fix.
                    git(wt, "checkout", task["parent"], "--", *task["source_files"])
                    full = pytest(python, wt, [], work / "junit" / f"{task['id']}-full.xml", args.full_timeout)
                    rec["full"] = {"rc": full["rc"], "n": full["n"], "seconds": full["seconds"],
                                   "failing": [f["id"] for f in full["failing"]]}
                    if full["rc"] not in (0, 1) or full["n"] == 0:
                        rec["reason"] = f"full suite doesn't run (rc={full['rc']})"
                        rec["tail"] = full["tail"][-600:]
                    else:
                        rec["valid"] = True
                        rec["fail_to_pass"] = sorted({f["id"] for f in before["failing"]})
        except subprocess.CalledProcessError as e:
            rec["reason"] = f"git failed: {e.stderr[-300:] if e.stderr else e}"
        finally:
            remove_worktree(repo, wt)
        state[task["id"]] = rec
        _dump(work / "validation.json", state)
        print(f"{task['id']}: {'VALID' if rec.get('valid') else 'invalid -- ' + rec.get('reason', '?')}"
              + (f" (suite {rec['full']['seconds']}s, {rec['full']['n']} tests)" if rec.get("valid") else ""),
              flush=True)
    tasks = []
    for c in selected():
        v = state[c["id"]]
        tasks.append({**c, "hidden_tests": [p for _, p in c["test_changes"]],
                      "fail_to_pass": v["fail_to_pass"], "baseline_failing": v["full"]["failing"],
                      "suite_seconds": v["full"]["seconds"], "suite_tests": v["full"]["n"]})
    _dump(work / "tasks.json", tasks)
    n_valid = sum(1 for v in state.values() if v.get("valid"))
    print(f"{len(tasks)} selected tasks ({n_valid} valid of {len(state)} validated) -> {work / 'tasks.json'}")


# ---- run -------------------------------------------------------------------

def _parse_stream(lines: list[str]) -> dict:
    tools: dict[str, int] = {}
    result: dict = {}
    hook_events, hook_context, hook_errors = 0, 0, []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind, sub = event.get("type"), event.get("subtype") or ""
        if kind == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tools[block["name"]] = tools.get(block["name"], 0) + 1
        elif kind == "result":
            result = event
        # --include-hook-events: one hook_response per hook run
        if kind == "system" and sub == "hook_response" and event.get("hook_event") == "PostToolUse":
            hook_events += 1
            if event.get("exit_code"):
                hook_errors.append((event.get("stderr") or event.get("output") or "")[-200:])
            elif "additionalContext" in (event.get("stdout") or ""):
                hook_context += 1
    usage = result.get("usage") or {}
    return {
        "tools": tools,
        "tool_calls": sum(tools.values()),
        "ziplex_calls": sum(n for k, n in tools.items() if k.startswith("mcp__ziplex")),
        "hook_events": hook_events,
        "hook_messages": hook_context,
        "hook_errors": hook_errors,
        "permission_denials": len(result.get("permission_denials") or []),
        "cost_usd": result.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_write": usage.get("cache_creation_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "turns": result.get("num_turns"),
        "duration_s": (result.get("duration_ms") or 0) / 1000,
        "is_error": result.get("is_error", True),
        "result_subtype": result.get("subtype"),
        "final_text": (result.get("result") or "")[-800:],
    }


def _claude_cmd(args, condition: str, wt: Path, cfg_dir: Path, python_fwd: str) -> list[str]:
    cmd = [args.claude, "-p", "--output-format", "stream-json", "--verbose", "--model", args.model,
           "--restricted", "--tools", "Read,Grep,Glob,Edit,Write,Bash", "--strict-mcp-config",
           "--no-session-persistence", "--max-budget-usd", str(args.max_budget),
           "--permission-mode", "acceptEdits", "--allowedTools", f"Bash({python_fwd} -m pytest:*)"]
    if condition in ("C1", "C2"):
        mcp = {"mcpServers": {"ziplex": {"command": args.ziplex_mcp, "args": ["--project", str(wt.resolve())]}}}
        _dump(cfg_dir / "mcp.json", mcp)
        cmd += ["--mcp-config", str(cfg_dir / "mcp.json"), "--allowedTools", "mcp__ziplex"]
    if condition == "C2":
        hook = {"hooks": {"PostToolUse": [{"matcher": "Edit|Write|MultiEdit",
                                           # forward slashes: hook commands may run under Git Bash on Windows
                                           "hooks": [{"type": "command", "command": args.ziplex_hook.replace("\\", "/"),
                                                      "timeout": 10}]}]}}
        _dump(cfg_dir / "settings.json", hook)
        cmd += ["--settings", str(cfg_dir / "settings.json"), "--include-hook-events"]
    return cmd


def run(args) -> None:
    work = Path(args.work)
    tasks = _load(work / "tasks.json", [])
    if args.tasks:
        wanted = args.tasks.split(",")
        tasks = [t for t in tasks if t["id"] in wanted]
    results = work / "results.jsonl"
    done = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["id"], r["condition"], r["rep"]))
    jobs = [(t, c, rep) for rep in range(args.reps) for t in tasks for c in args.conditions.split(",")]
    random.Random(args.seed).shuffle(jobs)  # interleave conditions against drift
    here = Path(__file__).resolve().parent
    for task, condition, rep in jobs:
        if (task["id"], condition, rep) in done:
            continue
        repo, python = Path(args.repos_root) / task["repo"], _python(args, task["repo"])
        python_fwd = python.replace("\\", "/")
        job = f"{task['id']}-{condition}-r{rep}"
        wt, cfg_dir = work / "wt" / job, work / "cfg" / job
        add_worktree(repo, wt, task["parent"])
        try:
            if condition in ("C1", "C2"):  # as `ziplex install claude` leaves it
                env = dict(os.environ, PYTHONPATH=str(here.parent / "src"))
                subprocess.run([sys.executable, "-m", "ziplex.cli", "index", str(wt)], check=True, env=env,
                               capture_output=True)
            cmd = _claude_cmd(args, condition, wt, cfg_dir, python_fwd)
            started = time.time()
            try:
                proc = subprocess.run(cmd, cwd=wt, input=PROMPT.format(task=task["task"], python=python_fwd),
                                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=args.timeout, env=test_env(wt))
                stdout, stderr = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as e:
                stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
                stderr = "timeout"
            (work / "streams").mkdir(parents=True, exist_ok=True)
            (work / "streams" / f"{job}.jsonl").write_text(stdout, encoding="utf-8")
            row = {"id": task["id"], "repo": task["repo"], "condition": condition, "rep": rep,
                   "wall_s": round(time.time() - started, 1), **_parse_stream(stdout.splitlines())}
            if row["is_error"] and not row["turns"]:
                # Auth/API failure before any work: don't score it, stop so the run can resume.
                sys.exit(f"claude run failed before doing anything ({job}): " + (stdout[-300:] or stderr[-300:]))
            if row["hook_errors"]:  # a broken hook command is a harness bug, not a C2 result
                sys.exit(f"hook failed in {job}: {row['hook_errors'][0]}")
            # The agent's change (everything but the index), before hidden tests overwrite anything.
            git(wt, "add", "-A", "--", ".", ":(exclude).ziplex")
            row["diff"] = git(wt, "diff", "--cached")
            stat = git(wt, "diff", "--cached", "--numstat")
            src_lines, src_files, test_files = 0, [], []
            for line in stat.splitlines():
                a, b, path = line.split("\t", 2)
                if is_test_path(path):
                    test_files.append(path)
                else:
                    src_files.append(path)
                    src_lines += (int(a) + int(b)) if a != "-" else 0
            row.update({"diff_files": src_files, "diff_lines": src_lines, "edited_tests": test_files})
            # Grade: hidden tests, then the full suite against the validated baseline.
            apply_tests(repo, wt, task)
            hidden = pytest(python, wt, _targets(task), work / "junit" / f"{job}-hidden.xml", args.test_timeout)
            row["solved"] = hidden["rc"] == 0
            row["hidden_failing"] = [f["id"] for f in hidden["failing"]]
            full = pytest(python, wt, [], work / "junit" / f"{job}-full.xml", args.test_timeout)
            hidden_files = set(task["hidden_tests"])
            baseline = set(task["baseline_failing"])
            row["regressions"] = sorted(f["id"] for f in full["failing"]
                                        if f["id"] not in baseline and f["file"] not in hidden_files)
            row["suite_rc"] = full["rc"]
            if full["n"] == 0:
                row["regressions"] = ["<suite did not run>"]
        finally:
            remove_worktree(repo, wt)
        with results.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{job}: solved={row['solved']} regr={len(row['regressions'])} calls={row['tool_calls']} "
              f"zx={row['ziplex_calls']} hook={row['hook_messages']} denied={row['permission_denials']} "
              f"${row['cost_usd']} {row['wall_s']}s", flush=True)


# ---- report ----------------------------------------------------------------

def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def _bootstrap(values: list[float], stat, n: int = 10000, seed: int = 0) -> tuple[float, float, float]:
    if not values:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    boots = sorted(stat(rng.choices(values, k=len(values))) for _ in range(n))
    return stat(values), boots[int(0.025 * n)], boots[int(0.975 * n)]


def report(args) -> None:
    work = Path(args.work)
    rows = [json.loads(l) for l in (work / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    conds = sorted({r["condition"] for r in rows})
    ids = sorted({r["id"] for r in rows})
    by: dict = {}
    for r in rows:
        by.setdefault((r["id"], r["condition"]), []).append(r)
    lines = [f"runs: {len(rows)} over {len(ids)} tasks"]
    for c in conds:
        cr = [r for r in rows if r["condition"] == c]
        k = sum(r["solved"] for r in cr)
        lo, hi = _wilson(k, len(cr))
        clean = sum(1 for r in cr if r["solved"] and not r["regressions"])
        lines.append(
            f"{c}: solved {k}/{len(cr)} ({k / len(cr):.2f}, Wilson 95% {lo:.2f}-{hi:.2f}) | solved w/o regressions "
            f"{clean}/{len(cr)} | runs with regressions {sum(1 for r in cr if r['regressions'])}/{len(cr)} | "
            f"median cost ${statistics.median(r['cost_usd'] or 0 for r in cr):.3f} | median calls "
            f"{statistics.median(r['tool_calls'] for r in cr)} | median time {statistics.median(r['duration_s'] for r in cr):.0f}s"
            + (f" | used ziplex {sum(1 for r in cr if r['ziplex_calls'])}/{len(cr)}" if c != "C0" else "")
            + (f" | hook fired {sum(1 for r in cr if r['hook_messages'])}/{len(cr)}" if c == "C2" else ""))
    for c in conds:
        if c == "C0":
            continue
        pairs = [(by[(i, "C0")], by[(i, c)]) for i in ids if (i, "C0") in by and (i, c) in by]
        if not pairs:
            continue
        diffs = [statistics.mean(x["solved"] for x in b) - statistics.mean(x["solved"] for x in a) for a, b in pairs]
        m, lo, hi = _bootstrap(diffs, statistics.mean)
        lines.append(f"{c}-C0 solve rate per task: mean {m:+.2f} (95% CI {lo:+.2f}..{hi:+.2f}) over {len(pairs)} tasks; "
                     f"better on {sum(d > 0 for d in diffs)}, worse on {sum(d < 0 for d in diffs)}")
        for metric in ("cost_usd", "tool_calls", "duration_s", "output_tokens"):
            ratios = []
            for a, b in pairs:
                ma, mb = statistics.median(x[metric] or 0 for x in a), statistics.median(x[metric] or 0 for x in b)
                if ma:
                    ratios.append(mb / ma)
            med, lo, hi = _bootstrap(ratios, statistics.median)
            lines.append(f"  {metric}: median {c}/C0 = {med:.2f} (95% CI {lo:.2f}-{hi:.2f})")
    lines.append("per task (solved/runs):")
    for i in ids:
        lines.append("  " + i + ": " + "  ".join(
            f"{c} {sum(x['solved'] for x in by.get((i, c), []))}/{len(by.get((i, c), []))}" for c in conds))
    text = "\n".join(lines)
    (work / "report.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mine")
    m.add_argument("--work", required=True)
    m.add_argument("--repos-root", required=True)
    m.add_argument("--repos", default="click,flask,rich,pygments")
    m.add_argument("--ref", default="origin/HEAD")
    m.add_argument("--commits", type=int, default=400)
    m.add_argument("--max-lines", type=int, default=60)
    m.add_argument("--no-gh", action="store_true")
    v = sub.add_parser("validate")
    v.add_argument("--work", required=True)
    v.add_argument("--repos-root", required=True)
    v.add_argument("--venvs-root", required=True)
    v.add_argument("--target", type=int, default=12)
    v.add_argument("--timeout", type=int, default=300)
    v.add_argument("--full-timeout", type=int, default=600)
    v.add_argument("--max-suite-seconds", type=float, default=120)
    v.add_argument("--max-per-repo", type=int, default=4)
    v.add_argument("--reject", action="append", help="id[:reason]; repeatable")
    r = sub.add_parser("run")
    r.add_argument("--work", required=True)
    r.add_argument("--repos-root", required=True)
    r.add_argument("--venvs-root", required=True)
    r.add_argument("--conditions", default="C0,C1,C2")
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--tasks", default=None, help="comma-separated task ids (default: all)")
    r.add_argument("--model", default="sonnet")
    r.add_argument("--claude", default="claude")
    r.add_argument("--ziplex-mcp", default="ziplex-mcp")
    r.add_argument("--ziplex-hook", default="ziplex-hook")
    r.add_argument("--max-budget", type=float, default=3.0)
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--test-timeout", type=int, default=900)
    r.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("report")
    p.add_argument("--work", required=True)
    args = parser.parse_args()
    {"mine": mine, "validate": validate, "run": run, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
