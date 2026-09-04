"""pack()'s checkpoint/resume system, split out of packager.py so that module
is left owning just the pipeline itself, not also this: saving/loading/
deleting a checkpoint file, deciding what to do when an LLM call has
exhausted its own retries (retry / answer manually / checkpoint and exit),
and the same question for a checkpoint found at the start of a run (resume
it / discard it and start over).

Every function here takes an `interactive` flag rather than calling
input()/print() unconditionally, so a non-interactive `pack` (CI, scripted
use, `--auto-correct`) degrades to a safe default -- checkpoint-and-exit for
a failing LLM call, always-resume for a found checkpoint -- instead of
EOFError-ing against closed stdin.
"""

import hashlib
import json
from pathlib import Path

from .file.textutil import relative_key as _rel_key
from .paths import REPO_ROOT

CHECKPOINT_DIR = REPO_ROOT / "checkpoint"


def _checkpoint_path(root_path: str) -> Path:
    """checkpoint/<basename>-<hash>.json -- the hash suffix (first 8 hex
    chars of sha256 of the resolved absolute path) keeps this filename
    collision-proof across two different projects that happen to share a
    folder name (e.g. C:\\clients\\acme\\backend and C:\\clients\\other\\
    backend). A basename-only filename doesn't: the second project's pack
    would find and silently auto-resume the first project's leftover
    checkpoint (non-interactive callers -- the GUI, always -- never even
    get a resume-vs-discard prompt to catch it), splicing one project's
    file summaries/signatures into the other's output.

    Safe to key on the raw absolute path here specifically because
    checkpoint.json is purely local, ephemeral tool bookkeeping -- deleted
    on a successful pack, never committed to the target project's own
    repo the way aif.json/detail.json/cache.json are (see README's Team
    use section) -- so there's no cross-machine-portability requirement
    the way there is for freshness.py's own mitigation of the same class
    of bug in the *committed* cache (which can't use a path fingerprint
    for exactly that reason).

    One consequence: a checkpoint already on disk under the pre-fix
    basename-only naming becomes unreachable once this ships -- accepted
    as a safe degrade (a pack resumes from scratch instead of picking up
    a stale in-flight checkpoint) rather than a real loss, since a
    checkpoint only ever holds re-derivable extraction state, never a
    project's actual source.
    """
    resolved_path = Path(root_path).resolve()
    digest = hashlib.sha256(str(resolved_path).encode("utf-8")).hexdigest()[:8]
    # resolved_path.name, not Path(root_path).name -- the latter is "" for a
    # relative root_path of "." (no name component at all), which used to
    # produce a checkpoint filename of just "-<hash>.json" for the common
    # "pack from inside the project's own folder" case. The hash alone is
    # what actually keeps this collision-proof (see docstring above); the
    # name is just a human-readable prefix, but it should still be one.
    return CHECKPOINT_DIR / f"{resolved_path.name}-{digest}.json"


def save_checkpoint(root_path: str, data: dict) -> None:
    CHECKPOINT_DIR.mkdir(exist_ok=True)
    path = _checkpoint_path(root_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n  💾 체크포인트 저장됨: {path}")


def load_checkpoint(root_path: str) -> dict | None:
    path = _checkpoint_path(root_path)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # A truncated/corrupted checkpoint (e.g. the process was killed
        # mid-write) shouldn't crash the caller -- list_checkpoints() below
        # already treats this the same way (surfaced as "읽기 실패", not
        # raised); resume_checkpoint_choice()'s callers need the same
        # "nothing usable here" outcome `path.exists()` being False already
        # gives, notably so `ziplex checkpoint clean <project_path>` can
        # still delete the very file it's failing to read.
        return None


def delete_checkpoint(root_path: str) -> None:
    path = _checkpoint_path(root_path)
    if path.exists():
        path.unlink()


def list_checkpoints() -> list[dict]:
    """Every leftover checkpoint file under CHECKPOINT_DIR -- `ziplex
    checkpoint list`'s data source. One dict per file: {"path",
    "project_name", "pending_files", "size_bytes", "modified" (mtime,
    epoch seconds)}.

    `project_name`/`pending_files` come from the checkpoint's own recorded
    `project.name`/`files_data`, not the filename -- the filename's hash
    suffix is a one-way sha256 digest (see _checkpoint_path()'s docstring),
    so the original project path can never be recovered from it alone;
    only what the checkpoint itself remembered at save time is available.

    A checkpoint file that fails to parse (hand-edited, truncated by a
    killed process mid-write) is still listed, with project_name
    "(읽기 실패)" and pending_files 0, rather than silently dropped or
    raising -- `ziplex checkpoint list` should surface a broken file the
    same way `ziplex checkpoint clean --all` needs to be able to remove
    one.
    """
    if not CHECKPOINT_DIR.is_dir():
        return []
    results = []
    for path in sorted(CHECKPOINT_DIR.glob("*.json")):
        try:
            stat = path.stat()
        except OSError:
            # Deleted between glob() and here (a concurrent successful
            # pack(), or `checkpoint clean --all` racing this listing) --
            # nothing left to report size/mtime for, so it's skipped
            # entirely rather than surfaced as a broken file: unlike the
            # read failure below, there's no file left for `ziplex
            # checkpoint clean` to point at.
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            project_name = data.get("project", {}).get("name") or "(알 수 없음)"
            pending_files = len(data.get("files_data") or {})
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            project_name = "(읽기 실패)"
            pending_files = 0
        results.append({
            "path": path,
            "project_name": project_name,
            "pending_files": pending_files,
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        })
    return results


def clear_all_checkpoints() -> int:
    """Deletes every checkpoint file under CHECKPOINT_DIR (`ziplex
    checkpoint clean --all`) and returns how many were removed. A no-op,
    not an error, when CHECKPOINT_DIR doesn't exist yet -- same "never
    raise over an absent target" spirit delete_checkpoint() itself already
    follows for a single missing file.
    """
    if not CHECKPOINT_DIR.is_dir():
        return 0
    removed = 0
    for path in CHECKPOINT_DIR.glob("*.json"):
        path.unlink()
        removed += 1
    return removed


def build_snapshot(root: Path, files_data: dict, rules: list = None, prompt: str = "", lang: str = "en") -> dict:
    """The shape handle_llm_failure() checkpoints on a failure -- everything
    pack() has produced so far, keyed by relative name (matching what
    unpack_snapshot() below expects to restore from), so a resumed run can
    skip straight past whatever already succeeded.

    lang (2026-08-26) records which packing-content language `rules`/
    `prompt`/each file's `summary` were actually written in -- so a later
    pack() resuming this checkpoint under a *different* `lang` (a forgotten
    `--lang` flag, a changed selection) can tell its own restored content is
    stale and needs regenerating instead of silently mixing languages under
    one `project.language` value. See unpack_snapshot()'s own docstring and
    packager.py's `lang_matches`.
    """
    return {
        # root.resolve().name, not root.name -- root itself stays unresolved
        # (needed as-is for _rel_key(fp, root) below), but resolving just for
        # the name avoids the same "" result Path(".").name gives.
        "project": {"name": root.resolve().name, "prompt": prompt, "language": lang},
        "rules": rules or [],
        "files_data": {
            _rel_key(fp, root): d
            for fp, d in files_data.items()
        }
    }


def unpack_snapshot(checkpoint: dict | None) -> tuple[list, str, dict, str]:
    """The read-side counterpart to build_snapshot() -- pulls (rules, prompt,
    files_data, lang) back out of a loaded checkpoint, so a caller restoring
    from one never has to know this shape's exact keys itself. Keeps the
    shape defined in exactly one place: a future change to build_snapshot()'s
    keys has to change this function right alongside it, in the same file,
    instead of silently drifting from a raw dict-indexing read site
    somewhere else that build_snapshot() has no visibility into.

    checkpoint=None (nothing to resume, e.g. load_checkpoint() found
    nothing) returns the same empty defaults an absent checkpoint already
    implied before this function existed -- lang defaults to "en" in that
    case too, matching pack()'s own default.

    lang defaults to "en" when reading a checkpoint saved before this field
    existed, same backward-compat convention `project.language`'s own
    missing-field default (packager.py/freshness.py) already uses.
    """
    if not checkpoint:
        return [], "", {}, "en"
    rules = checkpoint.get("rules", [])
    prompt = checkpoint.get("project", {}).get("prompt", "")
    files_data = checkpoint.get("files_data", {})
    lang = checkpoint.get("project", {}).get("language", "en")
    return rules, prompt, files_data, lang


def handle_llm_failure(
    name: str, field: str, current_aif: dict, root_path: str, interactive: bool = True
) -> str | None:
    """On an LLM call that's exhausted its own internal retries: ask the user
    what to do (retry / type a value / checkpoint and exit), or -- when
    interactive=False, e.g. under `pack --auto-correct` -- skip the prompt
    entirely and behave as if "checkpoint and exit" was chosen. That keeps a
    non-interactive `pack` call from blocking on input() forever (or crashing
    with EOFError, which is what used to happen here under a closed stdin);
    the caller gets a clean {} back and the checkpoint is there to resume
    from once the LLM is behaving again.
    """
    print(f"\n  ⚠️  {name} {field} 생성 실패")

    if not interactive:
        print("  💾 비대화형 모드 → 체크포인트 저장 후 중단")
        save_checkpoint(root_path, current_aif)
        return "EXIT"

    print("  [1] 재시도")
    print("  [2] 직접 입력")
    print("  [3] 저장 후 종료")
    choice = input("  선택: ").strip()

    if choice == "1":
        return None
    elif choice == "2":
        return input(f"  {field} 직접 입력: ").strip()
    elif choice == "3":
        save_checkpoint(root_path, current_aif)
        return "EXIT"

    return None


def resume_checkpoint_choice(interactive: bool) -> bool:
    """Returns True to resume from a found checkpoint, False to discard it and
    start over. Non-interactive callers always resume -- silently discarding
    prior progress is a worse default than continuing it, and there's no
    terminal to ask.
    """
    if not interactive:
        return True

    print(f"\n  📂 체크포인트 발견")
    print("  [1] 이어서 진행")
    print("  [2] 처음부터 시작")
    choice = input("  선택: ").strip()
    return choice != "2"
