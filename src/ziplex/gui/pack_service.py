"""Background pack-job runner backing gui_server.py's /api/pack* routes.

packager.pack() is long-running (real LLM calls, potentially minutes on a
real project) and talks to the user via print()/input() -- wrong to run
straight inside a Flask request handler. This module runs one pack in a
background thread and gives the routes something to poll and act on: a job
id, a running/reviewing/finalizing/done/error state, pack()'s own print()
lines captured into a per-job log list, and (once analysis finishes) a
pause point where a human reviews and corrects the result before it's
saved.

Interactive parity with the CLI's plain `pack <path>` (no --auto, no
--auto-correct), adapted for a browser instead of a terminal:

  - File selection: select_files() (file/selector.py) reads a number list
    off stdin, which doesn't exist over HTTP. list_selectable_files() runs
    the same collect_and_scan() step (config.py) up front so gui_server.py
    can show a human the safe/dangerous split as checkboxes; whatever they
    submit is passed into pack()'s new `preselected` parameter (see
    packager.py), which bypasses both `auto` and select_files() entirely.
  - Correction review: corrector.py's correct_aif() is a chain of input()
    prompts. Here, pack() runs with interactive=False (so a *repeated* LLM
    failure still can't block on stdin -- see checkpoint.handle_llm_failure()'s
    non-interactive path; that one gap is a known limitation, not something
    this module works around) but is *not*
    followed by an immediate finalize_aif(). Instead the job pauses in
    state "reviewing" with the raw aif (still carrying signatures/
    dependencies/confidence) attached, mirroring what correct_aif() would
    show a terminal user: project name/prompt, rules, and per-file
    summaries triaged by confidence.triage() into needs-review vs.
    auto-kept. submit_review() applies whatever the human changed through
    edits.py's pure setters -- the same seam corrector.py itself is built
    on -- then finalizes and saves, exactly like correct_aif() does at the
    end of its own flow.
  - Relationship editing: corrector.py's correct_relationships() is a
    number-prompt loop over file/relationship.py's move_file() -- reparent
    one file under another, stripping it from every existing reference
    first. The GUI deliberately does *not* mirror that: `dependencies` is a
    graph (a file can have more than one real parent, and cycles can
    already legitimately exist from raw Tree-sitter extraction), and an
    earlier drag-and-drop version built on move_file() silently severed a
    shared file's other references whenever one of its occurrences got
    dragged. add_dependency_in_job()/remove_dependency_in_job() wrap
    file/relationship.py's add_dependency()/remove_dependency() instead --
    a per-edge link/unlink pair that only ever touches the one file/target
    edge being edited, never any other file's list. get_review()'s `tree`
    (build_tree()'s shape) is what the GUI renders this over: a flat
    per-file list of outgoing edges, each internal one individually
    removable, plus a picker to link a new one.

Concurrency: each job gets its own `threading.Lock` (job["lock"]), acquired
for every read/mutation of that job's own fields (state/aif/log/result/
error) -- see _lookup_job()'s docstring. This means an operation on job A
(a status poll, a link/unlink edit, a cancel) never blocks behind an
operation on unrelated job B; the module-level `_jobs_lock` only guards
inserting into / looking up from the `_jobs` dict itself, which is quick
enough to never meaningfully contend. print()-capturing (see
_capture_for_job()) is the one deliberate exception: it serializes across
*all* jobs via a single `_stdout_capture_lock`, because sys.stdout is one
process-wide global no per-job lock can partition -- see that function's
docstring for why.

Jobs live in memory only (module-level dict) -- gone on server restart, same
lifetime as everything else gui_server.py holds (see its `_default_config`).
Fine for a single-user local tool; nothing here is meant to survive past one
GUI session.
"""

import contextlib
import json
import os
import threading
import uuid
from pathlib import Path

from .. import packager
from .. import settings as app_settings
from ..config import collect_and_scan
from ..confidence import triage
from ..edits import (
    finalize_aif,
    set_file_summary,
    set_folder_summary,
    set_project_name,
    set_project_prompt,
    set_rules,
)
from ..file.relationship import add_dependency as _add_dependency
from ..file.relationship import add_relationship as _add_relationship
from ..file.relationship import build_tree
from ..file.relationship import remove_dependency as _remove_dependency
from ..file.relationship import remove_relationship as _remove_relationship
from ..file.textutil import relative_key as _rel_key

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()  # guards _jobs itself (insert/lookup) only -- see module docstring

# One lock per aif.json path, not one global lock for every project -- same
# "operation on A never blocks behind operation on unrelated B" reasoning as
# _jobs_lock above. Guards every direct on-disk write to a given aif.json
# (link_saved_relationship()/unlink_saved_relationship() below, and
# submit_review()'s finalize-and-save): without this, two nearly-
# simultaneous writes to the same file -- a double-click, two browser tabs
# both on the Relationships page, or a live job's finalize racing a
# post-pack relationship edit aimed at the same output path -- could each
# read the same on-disk content, mutate their own in-memory copy, and write
# back, silently losing whichever wrote first. Found by code review: every
# *job* mutation already had this guard via job["lock"], but this on-disk
# path (which isn't a job at all) didn't.
_file_locks: dict[str, threading.Lock] = {}
_file_locks_meta_lock = threading.Lock()  # guards creating/evicting a per-path lock, not the write itself

# Bounds _file_locks the same way _MAX_FINISHED_JOBS bounds _jobs (a
# long-running GUI session touching many different aif.json paths shouldn't
# grow this forever) -- see _evict_old_file_locks() for why eviction has to
# skip a lock currently held, unlike job eviction.
_MAX_FILE_LOCKS = 200


def _evict_old_file_locks(keep: str):
    """Must be called with _file_locks_meta_lock already held. Only evicts
    *unlocked* entries other than `keep` (the path _lock_for_path() was just
    asked for): removing a Lock object a thread still holds wouldn't break
    that thread's own critical section, but a subsequent request for the
    same path would then create a brand new Lock and run concurrently with
    it -- silently defeating the whole point of this locking; excluding
    `keep` closes the same hole for the path being looked up *right now*,
    which would otherwise risk two callers getting two different Lock
    objects for it if eviction happened to remove it between them. Oldest
    first (dict insertion order); a net-negative round (everything else is
    currently held) just leaves the dict over size until the next call,
    rather than evicting something unsafe to evict.
    """
    if len(_file_locks) <= _MAX_FILE_LOCKS:
        return
    for key in list(_file_locks):
        if len(_file_locks) <= _MAX_FILE_LOCKS:
            break
        if key != keep and not _file_locks[key].locked():
            del _file_locks[key]


def _lock_for_path(path: str) -> threading.Lock:
    """Resolved to an absolute path first so 'aif.json' and './aif.json'
    from two different requests -- or a relative job output_path vs. an
    absolute one a GUI page happens to pass -- don't get two different
    locks for what's actually the same file. os.path.normcase() on top of
    that (a no-op on POSIX, lowercases on Windows) matters specifically for
    a path that doesn't exist on disk *yet*: Path.resolve() only normalizes
    an existing path's real on-disk case, so "Out.json" and "out.json"
    resolve to two *different* strings before the file is first created and
    only converge to the same one afterward -- verified directly. Without
    normcase(), the very first concurrent write to a brand-new file (the
    exact scenario this locking exists for) could still get two different
    Lock objects for what's actually the same eventual file.
    """
    key = os.path.normcase(str(Path(path).resolve()))
    with _file_locks_meta_lock:
        lock = _file_locks.setdefault(key, threading.Lock())
        _evict_old_file_locks(keep=key)
        return lock

# Bounds memory in a long-running GUI session -- a single-user local tool
# has no other trigger (no request ever asks to delete a finished job) to
# clean these up, and each one carries a full print()-captured log.
_MAX_FINISHED_JOBS = 20


def _evict_old_finished_jobs():
    """Must be called with _jobs_lock already held (start_pack_job() holds
    it for the insert anyway). Keeps at most _MAX_FINISHED_JOBS done/error
    jobs around, evicting the oldest first (dict insertion order, since
    _jobs entries are never reordered) -- jobs still running/reviewing/
    finalizing are never evicted regardless of count.

    Reads each job's own state under its own lock -- the same discipline
    has_reviewing_job() already follows -- since state is only ever *set*
    under job["lock"] elsewhere; the one race this still allows (evicting a
    job that finishes right after this check ran) is harmless, just
    deferred to the next call.
    """
    finished_ids = []
    for jid, job in _jobs.items():
        with job["lock"]:
            state = job["state"]
        if state in ("done", "error"):
            finished_ids.append(jid)
    excess = len(finished_ids) - _MAX_FINISHED_JOBS
    if excess > 0:
        for jid in finished_ids[:excess]:
            del _jobs[jid]

# contextlib.redirect_stdout reassigns the single process-wide sys.stdout,
# which isn't thread-safe on its own: two jobs whose print()-capturing
# blocks overlap in time (starting a second pack while an earlier one is
# still analyzing, or finalizing one job while another is still running)
# could each clobber the other's redirect, corrupting both jobs' logs, or
# worse, leaving sys.stdout stuck pointed at an already-finished job's
# writer. This lock makes that shared resource's use exclusive: only one
# job's capturing block runs at a time, process-wide. The cost is that two
# jobs' analysis phases can no longer log concurrently -- acceptable for a
# single-user local tool where running two packs at once is an edge case,
# not the primary workflow, and far simpler (and provably correct) than
# routing print() by thread identity, which would also require every
# ThreadPoolExecutor worker thread packager.pack() spins up internally to
# inherit that routing -- not something this module could arrange from the
# outside without packager.py's cooperation.
_stdout_capture_lock = threading.Lock()

# Same cap corrector.py's terminal prompt uses for a flagged file's shown
# signatures -- enough to judge the mismatch without dumping a huge list.
_SIGNATURES_SHOWN = 10


class _LogWriter:
    """Stands in for stdout during one job's captured block (see
    _capture_for_job()): buffers partial writes into complete lines and
    appends each to the job's log list under that job's own lock. Without
    this, pack()'s print() calls would go to the server process's own
    stdout, which a polling GUI client has no way to read.
    """

    def __init__(self, job: dict):
        self._job = job
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            with self._job["lock"]:
                self._job["log"].append(line)
        return len(s)

    def flush(self) -> None:
        pass


@contextlib.contextmanager
def _capture_for_job(job: dict):
    """contextlib.redirect_stdout(_LogWriter(job)), plus _stdout_capture_lock
    held for the duration -- see that lock's module-level comment for why a
    plain redirect_stdout isn't safe to use directly here.
    """
    with _stdout_capture_lock, contextlib.redirect_stdout(_LogWriter(job)):
        yield


def list_selectable_files(project_path: str) -> dict:
    """Runs config.collect_and_scan() over project_path and returns the
    safe/dangerous split -- the read-only step a GUI file-selection screen
    calls before a pack job exists at all, so a human sees the real file
    list (and *why* anything got flagged, not just that it did) instead of
    guessing. Whatever's checked from `safe` on submit becomes
    start_pack_job()'s `selected_files` -- and a `dangerous` entry's own
    `file` name can be checked too: naming one there is this screen's
    equivalent of the CLI's review_dangerous_files() prompt (see
    packager.pack()'s `preselected` handling, which trusts a name from
    either list equally), just as a checkbox a human ticks after seeing
    this same reason/matched-line detail instead of a terminal prompt.

    collect_and_scan() already loads project_path's .ziplex.json (config.py)
    the same way pack() itself does, so a file this screen never shows can't
    be re-included by submitting selected_files with its name in it --
    pack()'s own collection step wouldn't have produced it either, so it
    just silently wouldn't match anything in `preselected`.
    """
    scan_result = collect_and_scan(project_path)
    dangerous = sorted(
        ({**entry, "file": _rel_key(entry["file"], project_path)} for entry in scan_result["dangerous"]),
        key=lambda entry: entry["file"],
    )
    return {
        "safe": sorted(_rel_key(f, project_path) for f in scan_result["safe"]),
        "dangerous": dangerous,
        # What start_pack_job() below will actually use if the output-path
        # field is left blank -- "" when nothing's configured at either
        # settings.py layer, in which case packager.py's own RESULT_DIR
        # default applies (same as before settings.py existed). Surfaced
        # here so the pack form can show it instead of a human having to
        # guess where an unconfigured project's result will land.
        "default_output_path": app_settings.resolve_output_path(project_path, Path(project_path).name),
    }


def _build_review(aif: dict) -> dict:
    """The GUI-review-screen shape of an unfinalized aif: project name/
    prompt, rules, and per-file summaries split by confidence.triage() the
    same way corrector.py's correct_aif() splits them for a terminal --
    `needs_review` (low confidence, shown with its real signatures so a
    human can judge the mismatch without opening the file) and `auto_kept`
    (safe to leave alone, but still editable -- nothing here is read-only).
    """
    needs_review, auto_kept = triage(aif["files"])

    def entry(name: str, with_signatures: bool) -> dict:
        data = aif["files"][name]
        out = {
            "file": name,
            "confidence": data.get("confidence", 1.0),
            "summary": data.get("summary", ""),
        }
        if with_signatures:
            sigs = data.get("signatures", [])
            out["signatures"] = sigs[:_SIGNATURES_SHOWN]
            out["signatures_more"] = max(0, len(sigs) - _SIGNATURES_SHOWN)
        return out

    return {
        "project": dict(aif["project"]),
        "rules": list(aif["rules"]),
        "needs_review": [entry(name, with_signatures=True) for name in needs_review],
        "auto_kept": [entry(name, with_signatures=False) for name in auto_kept],
        # build_tree()'s shape: {file: {"internal": [...], "external": [...]}}
        # -- the same data finalize_aif() later ships as aif.json's
        # `relationships`, but fetched here (and recomputed live by
        # add_dependency_in_job()/remove_dependency_in_job() below) so the
        # GUI can render/edit it before the aif is saved.
        "tree": build_tree(aif["files"]),
        # No confidence.triage() equivalent for folders -- folder_summary.py
        # has no per-folder confidence signal, so every folder is shown for
        # review rather than only a flagged subset (a project's folder count
        # is always far smaller than its file count, so that's cheap).
        "folders": [
            {"folder": folder, "summary": data.get("summary", "")}
            for folder, data in aif.get("folders", {}).items()
        ],
    }


def _check_cancelled_for(job: dict):
    """Builds packager.pack()'s `check_cancelled` callback for one job --
    reads (and clears) `job["cancel_action"]` under `job["lock"]`, the same
    per-job-lock discipline every other field on this dict already follows.
    Clearing it on read (not just on act) means a stray second poll tick
    that raced the request in only ever sees it once -- pack() itself stops
    at the very next checkpoint after the first non-None answer regardless,
    so there's nothing left to consume a second time in practice, but this
    keeps that true by construction rather than by accident. Separately
    records the answer into `job["cancel_result"]` (never cleared) so
    _run() can tell *why* pack() came back empty after the fact, once
    outside the loop that's actually polling this.
    """
    def check() -> str | None:
        with job["lock"]:
            action = job.get("cancel_action")
            if action:
                job["cancel_action"] = None
                job["cancel_result"] = action
            return action
    return check


def request_cancel(job_id: str, save: bool) -> bool:
    """Asks a "running" job to stop at its next check_cancelled() checkpoint
    (see packager.pack()'s own docstring for where those are) -- the only
    way to interrupt a background pack thread at all, since a real Python
    thread can't be safely killed from outside once started. Returns False
    (no-op) if job_id is unknown or the job isn't currently "running" --
    cancel_job() is the equivalent for a "reviewing" job, which has nothing
    actually running to stop.

    Not instant: `_run()`'s log keeps growing (and /api/pack/status keeps
    reporting "running") until pack() actually reaches its next checkpoint
    and returns -- a caller should keep polling the normal way rather than
    assuming an immediate state change.
    """
    try:
        job = _lookup_job(job_id)
    except ValueError:
        return False
    with job["lock"]:
        if job["state"] != "running":
            return False
        job["cancel_action"] = "save" if save else "discard"
    return True


def _run(
    job: dict, project_path: str, no_cache: bool, no_llm: bool, selected_files: list[str],
    result_dir: Path | None = None, lang: str = "en", resume: bool = False,
) -> None:
    try:
        with _capture_for_job(job):
            aif = packager.pack(
                project_path, interactive=False, use_cache=not no_cache, use_llm=not no_llm,
                preselected=selected_files, check_cancelled=_check_cancelled_for(job),
                result_dir=result_dir, lang=lang,
                # resume (only ever True for the error screen's "다시 시도"
                # button, see start_pack_job()'s own docstring) forces a
                # leftover checkpoint to always be resumed regardless of
                # no_cache -- a fresh "패킹 시작" submission leaves this
                # False, so use_cache's own "완전히 재패킹 discards a stale
                # checkpoint" behavior is unaffected there.
                discard_checkpoint=False if resume else None,
            )
            if not aif:
                with job["lock"]:
                    cancel_result = job.get("cancel_result")
                # pack() returns {} for a few different reasons -- nothing
                # was selected, a repeated LLM failure hit
                # handle_llm_failure()'s non-interactive path, or (new)
                # request_cancel() asked it to stop. cancel_result (only
                # ever set by the third case) is what tells these apart
                # after the fact, since pack() itself just returns the same
                # empty {} either way.
                if cancel_result == "save":
                    raise RuntimeError("사용자가 취소함 -- 체크포인트에 저장됨 (다음 실행 시 이어서 진행 가능)")
                if cancel_result == "discard":
                    raise RuntimeError("사용자가 취소함 -- 저장하지 않음")
                raise RuntimeError(
                    "패킹이 완료되지 않았습니다 (선택된 파일이 없거나, "
                    "반복된 LLM 오류로 체크포인트에 저장 후 중단됨). 로그를 확인하세요."
                )
        with job["lock"]:
            job["state"] = "reviewing"
            job["aif"] = aif
    except Exception as e:
        with job["lock"]:
            job["state"] = "error"
            job["error"] = str(e)


def start_pack_job(
    project_path: str, output_path: str | None = None, no_cache: bool = False, no_llm: bool = False,
    selected_files: list[str] | None = None, lang: str = "en", resume: bool = False,
) -> str:
    """Kicks off one pack() run in a background thread and returns its job id
    immediately. selected_files (relative names, from list_selectable_files()'s
    "safe" list) becomes pack()'s `preselected` -- see this module's docstring
    for why file selection has to happen before the job starts rather than
    inside it. Omitting selected_files (None) is treated the same as an empty
    list here -- packs nothing -- rather than falling through to pack()'s own
    auto/interactive fallback for a bare `preselected=None`; gui_server.py's
    route already 400s before ever calling this with no selection, so this
    only matters for a future caller that skips that guard.

    no_llm mirrors `pack --no-llm` (CLI): no GEMINI_API_KEY/network call at
    all, `rules` stays `[]`, `prompt` becomes packager.STRUCTURAL_ONLY_NOTE,
    and each file's summary comes from summarizer.generate_structural_
    summaries() instead of an LLM. The review screen (get_review()) needs no
    changes for this -- confidence.triage() and the summary-edit fields
    already just render whatever `pack()` produced, structural or not.

    lang mirrors CLI `pack --lang` -- passed straight through to
    packager.pack()'s own `lang` param, see its docstring for what it
    controls (the language every LLM-written value, plus Ziplex's own fixed
    strings, is actually written in). "en" is both the default and the
    recommended choice, matching the pack form's own dropdown.

    output_path resolution (settings.py): a caller-given output_path (the
    pack form's "출력 경로" field, non-blank) is used as-is for this run and
    also pins this project to its parent folder going forward
    (set_project_output_dir()) -- an explicit choice, remembered. Left blank,
    it resolves through settings.py instead (this project's own pin, else
    the global default) into a concrete path if either is configured, or
    stays None (packager.py's own RESULT_DIR-based default applies,
    unchanged from before settings.py existed) if neither is. Either way,
    the same folder that's resolved here is what packager.pack()'s
    `result_dir` is given too, so its incremental-reuse cache lookup during
    this run and the actual save destination afterward (submit_review(),
    via resolve_output_path()/save_aif()) never drift apart.

    The job pauses in state "reviewing" once analysis finishes; see
    get_review()/submit_review().

    resume (default False) is set only by the error screen's "다시 시도"
    button reposting a failed job's own retry_params -- see get_job_status()
    -- to force packager.pack()'s discard_checkpoint=False, so a retry
    always resumes whatever checkpoint that same job's own failure just
    saved, regardless of what no_cache says. A fresh "패킹 시작" submission
    never sets this, so "완전히 재패킹" still discards a genuinely stale
    leftover checkpoint from an unrelated earlier run the normal way -- see
    packager.pack()'s own discard_checkpoint docstring for the full story
    (a real bug reported directly: retrying a job that started with
    "완전히 재패킹" checked used to re-bill every file's summary on every
    single retry, discarding the very checkpoint the retry existed to use).
    """
    # Exactly what the caller passed, before output_path gets resolved into
    # a concrete path below -- get_job_status()'s retry_params echoes this
    # one back (not the resolved output_path), so a "retry this failed job"
    # request that started out blank doesn't get treated as an explicit
    # path on retry and silently pin the project (see set_project_output_dir()
    # just below -- only ever supposed to fire on a genuinely explicit
    # value, never one this function resolved on the caller's behalf).
    original_output_path = output_path
    if output_path:
        app_settings.set_project_output_dir(project_path, str(Path(output_path).parent))
    else:
        output_path = app_settings.resolve_output_path(project_path, Path(project_path).name) or None
    result_dir = Path(output_path).parent if output_path else None

    job_id = uuid.uuid4().hex
    job = {
        "state": "running",
        "log": [],
        "result": None,
        "error": None,
        "aif": None,
        "project_path": project_path,
        "output_path": output_path,
        # Stashed only so get_job_status() can hand back a retry_params
        # payload identical in shape to what gui_server.py's /api/pack
        # route accepts -- a failed job's error screen can then just repost
        # this verbatim (see js/pack.js's showErrorState()) instead of
        # sending a human back to reselect files/retype paths from scratch,
        # letting pack()'s own non-interactive checkpoint auto-resume (see
        # checkpoint.resume_checkpoint_choice()) pick up where it left off.
        "no_cache": no_cache,
        "no_llm": no_llm,
        "selected_files": selected_files or [],
        "lang": lang,
        "retry_output_path": original_output_path,
        "lock": threading.Lock(),
        "cancel_action": None,  # set by request_cancel(), consumed by _check_cancelled_for()
        "cancel_result": None,  # set once cancel_action is consumed, read by _run() after pack() returns
    }
    with _jobs_lock:
        _jobs[job_id] = job
        _evict_old_finished_jobs()
    thread = threading.Thread(
        target=_run, args=(job, project_path, no_cache, no_llm, selected_files or [], result_dir, lang, resume),
        daemon=True,
    )
    thread.start()
    return job_id


def _lookup_job(job_id: str) -> dict:
    """Looks up a job by id under `_jobs_lock`. Raises ValueError if unknown.
    The returned dict is a stable reference -- jobs are only ever mutated in
    place, never replaced or removed from `_jobs` -- so nothing needs to
    keep holding `_jobs_lock` past this lookup; callers acquire the job's
    own `lock` afterward for whatever they actually do with it, which is
    what keeps unrelated jobs from contending with each other at all (see
    the module docstring's Concurrency section).
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise ValueError(f"알 수 없는 job_id: {job_id}")
    return job


def get_job_status(job_id: str, since: int = 0) -> dict | None:
    """None if job_id is unknown (typo, or the server restarted since the job
    was started -- jobs don't persist). Otherwise the job's current state
    plus only the log lines from index `since` onward, so a polling client
    can pass back the length it already has instead of re-fetching the whole
    log (potentially thousands of lines on a large project) on every poll.

    retry_params is start_pack_job()'s own argument shape (project_path/
    output_path/no_cache/no_llm/selected_files/lang) echoed straight back --
    always present, not just on "error", since it costs nothing to include
    and keeps this function's shape uniform. Its real purpose is the error
    state: a repeated LLM failure lands the job in "error" via
    checkpoint.handle_llm_failure()'s non-interactive default (checkpoint
    and stop -- see packager.py), and until this existed the only way
    forward from that screen was abandoning the job and redoing file
    selection from scratch. Reposting this verbatim to /api/pack instead
    lets pack()'s own checkpoint-resume logic (resume_checkpoint_choice(),
    always-yes when non-interactive) pick up from exactly where it left
    off.
    """
    try:
        job = _lookup_job(job_id)
    except ValueError:
        return None
    with job["lock"]:
        return {
            "state": job["state"],
            "log": job["log"][since:],
            "log_len": len(job["log"]),
            "result": job["result"],
            "error": job["error"],
            "retry_params": {
                "project_path": job["project_path"],
                "output_path": job["retry_output_path"],
                "no_cache": job["no_cache"],
                "no_llm": job["no_llm"],
                "selected_files": job["selected_files"],
                "lang": job["lang"],
            },
        }


def get_review(job_id: str) -> dict | None:
    """None if job_id is unknown or the job hasn't reached "reviewing" yet
    (still running, being finalized, or already done/errored) -- the review
    payload only exists in that one window. Recomputed fresh from the job's
    live `aif` on every call rather than cached, so it always reflects any
    edits already applied via add_dependency_in_job()/remove_dependency_in_job()
    -- an earlier version cached this at the moment the job first paused and
    never invalidated it, so a second fetch (e.g. a page reload) could show
    a relationship graph that no longer matched what was actually about to
    be saved.
    """
    try:
        job = _lookup_job(job_id)
    except ValueError:
        return None
    with job["lock"]:
        if job["state"] != "reviewing":
            return None
        return _build_review(job["aif"])


def has_reviewing_job() -> bool:
    """True if any job is currently paused in "reviewing" -- backs
    gui_server.py's native-window close guard. js/pack.js's own beforeunload
    handler (see showReviewState()'s comment) only fires on an in-page
    reload/navigation; clicking the OS window's close button destroys the
    webview directly without ever running the page's unload event, so
    nothing browser-side catches a close there. This Python-side check is
    what closes that gap, by asking pack_service directly rather than
    trying to synchronize with page-side JS state from the close handler.

    Locking follows the same discipline _lookup_job()'s docstring
    describes: never hold `_jobs_lock` while acquiring a job's own lock, so
    this can't deadlock against a concurrent request touching one job.
    """
    with _jobs_lock:
        jobs = list(_jobs.values())
    for job in jobs:
        with job["lock"]:
            if job["state"] == "reviewing":
                return True
    return False


def _require_reviewing(job: dict) -> None:
    """Must be called with job["lock"] already held. Raises ValueError if
    the job isn't currently paused in "reviewing" with a live `aif` --
    anything else means there's nothing valid to mutate. Checking this and
    the actual mutation under the same lock acquisition (every caller below
    does) is what closes two races an earlier version of this module had:
    cancel_job() swapping `aif` out from under a concurrent mutation, and
    two concurrent mutations both passing a check (e.g. add_dependency()'s
    cycle guard) before either had actually written anything yet.
    """
    if job["state"] != "reviewing" or job["aif"] is None:
        raise ValueError(f"이 작업은 검토 대기 상태가 아닙니다 (현재: {job['state']})")


def add_dependency_in_job(job_id: str, file_name: str, target: str) -> dict:
    """Adds the dependency edge file_name -> target in a "reviewing" job's
    paused aif -- the "link" backend for the GUI review screen's per-edge
    relationship editor, wrapping file/relationship.add_dependency(). Only
    file_name's own edges change; see that function's docstring for why
    that matters (a file can have more than one real parent). Returns the
    recomputed tree (build_tree()'s shape) so the caller can redraw without
    a full get_review() round trip.

    Raises ValueError for an unknown job_id, a job not currently
    "reviewing", or an unknown/self file_name/target; CycleError if the edge
    would create a dependency cycle. Both are left for the caller
    (gui_server.py) to turn into the right HTTP status rather than being
    swallowed here.
    """
    job = _lookup_job(job_id)
    with job["lock"]:
        _require_reviewing(job)
        _add_dependency(job["aif"]["files"], file_name, target)
        return build_tree(job["aif"]["files"])


def remove_dependency_in_job(job_id: str, file_name: str, target: str) -> dict:
    """Removes the dependency edge file_name -> target in a "reviewing"
    job's paused aif -- the "unlink" counterpart to add_dependency_in_job(),
    wrapping file/relationship.remove_dependency() the same way. Returns the
    recomputed tree. Raises ValueError for an unknown job_id, a job not
    currently "reviewing", or an unknown file_name.
    """
    job = _lookup_job(job_id)
    with job["lock"]:
        _require_reviewing(job)
        _remove_dependency(job["aif"]["files"], file_name, target)
        return build_tree(job["aif"]["files"])


def _edit_saved_relationships(aif_path: str, edit) -> dict:
    """Shared load/mutate/save for link_saved_relationship()/
    unlink_saved_relationship() below -- `edit(relationships) -> relationships`
    does the actual add_relationship()/remove_relationship() call.

    Deliberately does NOT go through packager.save_aif(): that function
    expects the *in-progress* pack shape (splitting each file's `compressed`
    into detail.json, `_manifest` into cache.json -- see its own docstring),
    neither of which exists any more on an aif.json already written by a
    finished pack. Calling it on a freshly-reloaded, already-finalized aif
    would overwrite detail.json with empty `compressed` bodies for every
    file and wipe cache.json's manifest, purely as a side effect of editing
    one relationship. This instead reads and rewrites aif.json alone, byte
    for byte identical apart from the `relationships` field, leaving
    detail.json/cache.json untouched.

    Guarded by _lock_for_path() (see its own comment) so the read-modify-
    write can't race a concurrent edit to the same file.
    """
    with _lock_for_path(aif_path):
        with open(aif_path, "r", encoding="utf-8") as f:
            aif = json.load(f)

        relationships = edit(aif.get("relationships", {}))
        aif["relationships"] = relationships

        with open(aif_path, "w", encoding="utf-8") as f:
            json.dump(aif, f, ensure_ascii=False, indent=2)

        return relationships


def link_saved_relationship(aif_path: str, file_name: str, target: str) -> dict:
    """add_dependency_in_job()'s counterpart for a project that's already
    been packed and saved -- no live job involved at all, just aif_path on
    disk. Lets a human fix a relationship they notice is wrong *after*
    packing without re-running the whole pipeline, from the same read-only
    browse pages query_service.py's routes already serve. Wraps
    file/relationship.add_relationship() (the `relationships`-shaped
    counterpart to add_dependency(), which has nothing left to edit once
    finalize_aif() has already pruned `dependencies` -- see that function's
    own docstring). Raises the same ValueError/CycleError add_relationship()
    does; OSError/json.JSONDecodeError if aif_path can't be read.
    """
    return _edit_saved_relationships(aif_path, lambda rel: _add_relationship(rel, file_name, target))


def unlink_saved_relationship(aif_path: str, file_name: str, target: str) -> dict:
    """remove_dependency_in_job()'s counterpart for an already-saved
    project -- see link_saved_relationship()'s docstring for the full
    reasoning. Wraps file/relationship.remove_relationship().
    """
    return _edit_saved_relationships(aif_path, lambda rel: _remove_relationship(rel, file_name, target))


def cancel_job(job_id: str) -> bool:
    """Discards a job waiting in "reviewing" without saving anything.
    Returns False (no-op) if job_id is unknown or it isn't in that state --
    a running job can't be cancelled mid-analysis, and a done/errored/
    finalizing one has nothing left to cancel (a submit_review() already in
    flight has already moved a job out of "reviewing" -- see its own
    docstring for why that ordering matters).
    """
    try:
        job = _lookup_job(job_id)
    except ValueError:
        return False
    with job["lock"]:
        if job["state"] != "reviewing":
            return False
        job["state"] = "error"
        job["error"] = "사용자가 취소함"
        job["aif"] = None
    return True


def submit_review(
    job_id: str,
    project_name: str | None = None,
    project_prompt: str | None = None,
    rules: list[str] | None = None,
    summaries: dict[str, str] | None = None,
    folder_summaries: dict[str, str] | None = None,
) -> dict:
    """Applies GUI-submitted corrections to a "reviewing" job's paused aif,
    then finalizes and saves it -- the non-terminal equivalent of
    corrector.py's correct_aif(), built on the same edits.py setters it
    uses. Returns the {"aif_path", "project_path"} result on success.

    Raises ValueError if job_id is unknown or the job isn't currently
    waiting for review (already finalized, still running, or errored/
    cancelled) -- there's nothing valid to apply corrections to in any of
    those states.
    """
    job = _lookup_job(job_id)
    with job["lock"]:
        _require_reviewing(job)
        aif = job["aif"]

        if project_name:
            set_project_name(aif, project_name)
        if project_prompt:
            set_project_prompt(aif, project_prompt)
        if rules is not None:
            set_rules(aif, rules)
        for name, summary in (summaries or {}).items():
            if summary and name in aif["files"]:
                set_file_summary(aif, name, summary)
        for folder, summary in (folder_summaries or {}).items():
            if summary and folder in aif.get("folders", {}):
                set_folder_summary(aif, folder, summary)

        # From here on `aif` is a reference this call alone owns.
        # finalize_aif()/save_aif() below do real I/O (save_aif() prints,
        # which itself needs this job's own lock via _LogWriter), so they
        # have to run outside job["lock"] to avoid deadlocking against it --
        # moving the job out of "reviewing" first is what keeps that window
        # safe: every mutator above (cancel_job, add_dependency_in_job,
        # remove_dependency_in_job) is gated on state == "reviewing", so none
        # of them can touch this job's `aif` while it's being committed here.
        job["state"] = "finalizing"

    # Computed before the save, not after, via the same
    # packager.resolve_output_path() save_aif() itself uses internally --
    # not a separate copy of that fallback, so the two can't silently drift
    # apart. The lock below has to key on the exact file this write actually
    # lands on: a link_saved_relationship()/unlink_saved_relationship() call
    # aimed at the same path (someone browsing this project's *previous*
    # pack via the Relationships page while this job finishes) has to
    # contend for the same lock, not a different one derived from a stale
    # name.
    output_path = job["output_path"]
    result_path = packager.resolve_output_path(aif, output_path)

    try:
        with _lock_for_path(str(result_path)), _capture_for_job(job):
            aif = finalize_aif(aif)
            packager.save_aif(aif, output_path)
    except Exception as e:
        with job["lock"]:
            job["state"] = "error"
            job["error"] = str(e)
            job["aif"] = None
        raise

    result = {"aif_path": str(result_path), "project_path": job["project_path"]}
    with job["lock"]:
        job["state"] = "done"
        job["result"] = result
        job["aif"] = None  # no longer needed, and keeps the job dict small
    return result
