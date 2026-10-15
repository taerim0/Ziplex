"""Changed files from git, as project-relative paths.

spec: "worktree" (default) = uncommitted changes incl. staged and untracked
files; "staged" = the index only; anything else is passed to `git diff
--name-status` as-is ("HEAD~1", "main...", "abc123 def456")."""
import posixpath
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Changes:
    modified: list[str] = field(default_factory=list)   # added, modified, or renamed-to
    deleted: list[str] = field(default_factory=list)


def _git(project: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", project, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def changed_files(project: str, spec: str = "worktree") -> Changes:
    top = Path(_git(project, "rev-parse", "--show-toplevel").strip()).resolve()
    root = Path(project).resolve()
    try:
        prefix = root.relative_to(top).as_posix()
    except ValueError:
        raise ValueError(f"{project} is not inside its git work tree {top}")
    prefix = "" if prefix == "." else prefix + "/"

    if spec == "worktree":
        status = _git(project, "diff", "--name-status", "-M", "HEAD")
        untracked = _git(project, "ls-files", "--others", "--exclude-standard", "--full-name")
    elif spec == "staged":
        status = _git(project, "diff", "--name-status", "-M", "--cached")
        untracked = ""
    else:
        status = _git(project, "diff", "--name-status", "-M", *spec.split())
        untracked = ""

    changes = Changes()

    def add(bucket: list[str], git_path: str) -> None:
        git_path = git_path.strip().replace("\\", "/")
        if git_path.startswith(prefix):
            rel = posixpath.normpath(git_path[len(prefix):])
            if rel not in bucket:
                bucket.append(rel)

    for line in status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0][:1]
        if code == "D":
            add(changes.deleted, parts[1])
        elif code == "R":
            add(changes.deleted, parts[1])
            add(changes.modified, parts[2])
        else:
            add(changes.modified, parts[-1])
    for line in untracked.splitlines():
        if line.strip():
            add(changes.modified, line)
    return changes
