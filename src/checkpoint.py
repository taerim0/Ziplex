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

from file.textutil import relative_key as _rel_key

CHECKPOINT_DIR = Path(__file__).parent.parent / "checkpoint"


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
    resolved = str(Path(root_path).resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:8]
    return CHECKPOINT_DIR / f"{Path(root_path).name}-{digest}.json"


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
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_checkpoint(root_path: str) -> None:
    path = _checkpoint_path(root_path)
    if path.exists():
        path.unlink()


def build_snapshot(root: Path, files_data: dict, rules: list = None, prompt: str = "") -> dict:
    """The shape handle_llm_failure() checkpoints on a failure -- everything
    pack() has produced so far, keyed by relative name (matching what
    unpack_snapshot() below expects to restore from), so a resumed run can
    skip straight past whatever already succeeded.
    """
    return {
        "project": {"name": root.name, "prompt": prompt},
        "rules": rules or [],
        "files_data": {
            _rel_key(fp, root): d
            for fp, d in files_data.items()
        }
    }


def unpack_snapshot(checkpoint: dict | None) -> tuple[list, str, dict]:
    """The read-side counterpart to build_snapshot() -- pulls (rules, prompt,
    files_data) back out of a loaded checkpoint, so a caller restoring from
    one never has to know this shape's exact keys itself. Keeps the shape
    defined in exactly one place: a future change to build_snapshot()'s keys
    has to change this function right alongside it, in the same file,
    instead of silently drifting from a raw dict-indexing read site
    somewhere else that build_snapshot() has no visibility into.

    checkpoint=None (nothing to resume, e.g. load_checkpoint() found
    nothing) returns the same empty defaults an absent checkpoint already
    implied before this function existed.
    """
    if not checkpoint:
        return [], "", {}
    rules = checkpoint.get("rules", [])
    prompt = checkpoint.get("project", {}).get("prompt", "")
    files_data = checkpoint.get("files_data", {})
    return rules, prompt, files_data


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
