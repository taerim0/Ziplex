"""Live filesystem watching for the GUI's staleness badge -- see
src/gui/AGENTS.md for the fuller design rationale. Backs gui_server.py's
/api/watch/start and /api/watch/status routes.

Before this existed, freshness (_stale_warning() in query_service.py) was
computed once per page load and never again -- a file changed while the
Overview/Files page stayed open just didn't show up until the human
reloaded or re-navigated. This makes that check live instead of on-demand,
using watchdog (OS-level file-system-change notifications -- inotify,
FSEvents, or ReadDirectoryChangesW depending on platform, not polling), so
CPU/memory cost while idle is effectively zero. The real cost is one
background OS-notification thread per watched project plus a cheap
debounced recompute (the same freshness.check_freshness() the on-demand
"_stale" field already uses) whenever something relevant actually changes
-- not a performance concern for a single-user local tool watching one
project directory at a time.

One watcher per project path, not one global watcher for "whatever's
currently open" -- start_watch() is idempotent (replaces an existing
watcher for the same project rather than stacking a second one), and
MAX_WATCHERS bounds a long GUI session that's opened many different
projects over time, the same reasoning pack_service.py's job/lock
eviction already applies elsewhere in this codebase.
"""

import json
import threading
import time
from pathlib import Path

import pathspec
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ..file.collector import DEFAULT_IGNORE
from ..freshness import check_freshness_scoped

# A single save can fire several OS events in quick succession (some
# editors delete+recreate rather than write in place), and a git checkout/
# branch switch can touch hundreds of files at once -- debouncing collapses
# a whole burst into at most one recompute, DEBOUNCE_SECONDS after the
# burst goes quiet, rather than one recompute per individual event.
DEBOUNCE_SECONDS = 1.0

MAX_WATCHERS = 5

_watchers: dict[str, dict] = {}
_watchers_lock = threading.Lock()
# Serializes start_watch()'s whole stop-old/build-new/register-new sequence
# below -- a separate lock from _watchers_lock (which only ever guards a
# single dict read/write) so that recompute(), called after this lock is
# released, can still take _watchers_lock itself without deadlocking.
# Without this, two near-simultaneous start_watch() calls for the same
# project (two tabs, or app.js firing on every Overview/Files mount) could
# both pop the same existing entry (only one gets it, so the other's
# "existing" is None -- no double-stop), both build+start their own new
# Observer entirely outside any lock, and then race on the final dict
# insert -- the loser's Observer is overwritten with nothing left to stop
# it, leaking that thread indefinitely, unrecoverable even by
# MAX_WATCHERS eviction.
_start_lock = threading.Lock()


def _abs_key(project_path: str) -> str:
    return str(Path(project_path).resolve())


def _load_manifest(aif_path: str) -> dict:
    cache_path = Path(aif_path).with_name(f"{Path(aif_path).stem}.cache.json")
    with open(cache_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_ignore_spec(root: Path) -> pathspec.PathSpec:
    """Same construction file/collector.py's collect_files() uses --
    DEFAULT_IGNORE plus the project's own .gitignore -- built once per
    watch, not per event. Not needed for *correctness*: check_freshness()
    already only ever re-hashes collect_and_scan()'s own already-filtered
    safe-file list, so an event from an ignored path triggering a
    recompute would just correctly find nothing changed. Purely to avoid
    an unnecessary recompute for every internal .git/node_modules/venv
    write those directories generate constantly on their own, which would
    otherwise still get scheduled (debounced, but still eventually run)
    for filesystem churn that was never going to affect the result.
    """
    patterns = DEFAULT_IGNORE.copy()
    gitignore_path = root / ".gitignore"
    if gitignore_path.exists():
        try:
            with open(gitignore_path, "r", encoding="utf-8") as f:
                patterns.extend(line.strip() for line in f if line.strip() and not line.startswith("#"))
        except OSError:
            pass
    return pathspec.PathSpec.from_lines("gitignore", patterns)


class _DebouncedHandler(FileSystemEventHandler):
    """Filters out irrelevant (ignored-path, directory) events, then
    debounces the rest into at most one on_change() call per
    DEBOUNCE_SECONDS of quiet.
    """

    def __init__(self, root: Path, ignore_spec: pathspec.PathSpec, on_change):
        self._root = root
        self._ignore_spec = ignore_spec
        self._on_change = on_change
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()

    def _relevant(self, path: str) -> bool:
        try:
            relative = str(Path(path).relative_to(self._root))
        except ValueError:
            return True  # not under root somehow -- err toward recomputing rather than silently missing it
        return not self._ignore_spec.match_file(relative)

    def _schedule(self):
        with self._timer_lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._on_change)
            self._timer.daemon = True
            self._timer.start()

    def on_any_event(self, event):
        if event.is_directory:
            return
        if not self._relevant(event.src_path):
            return
        self._schedule()


def _evict_old_watchers(keep: str):
    """Must be called with _watchers_lock already held. Stops and drops
    the oldest-started watcher(s) once MAX_WATCHERS is exceeded, `keep`
    (the one just (re)started) always spared.
    """
    if len(_watchers) <= MAX_WATCHERS:
        return
    for key in sorted(_watchers, key=lambda k: _watchers[k]["started_at"]):
        if len(_watchers) <= MAX_WATCHERS:
            break
        if key == keep:
            continue
        _watchers.pop(key)["observer"].stop()


def start_watch(project_path: str, aif_path: str) -> None:
    """(Re)starts watching project_path for changes relative to aif_path's
    sibling cache.json manifest. Idempotent: replaces (stops, then
    restarts) any existing watcher already covering this same project,
    rather than stacking a second Observer on top of it -- a GUI page
    calling this once per visit (see js/app.js's startStaleWatch()) can't
    leak observers by navigating back and forth.

    Populates an initial report synchronously before returning, so the
    very first /api/watch/status poll already has something real instead
    of null -- otherwise a human would see the badge disappear for a few
    seconds after opening a project that was already known to be stale.
    """
    key = _abs_key(project_path)
    root = Path(project_path)

    def recompute():
        try:
            manifest = _load_manifest(aif_path)
            report = check_freshness_scoped(project_path, manifest)
            with _watchers_lock:
                entry = _watchers.get(key)
                if entry is not None:
                    entry["report"] = {
                        "is_stale": report.is_stale,
                        "changed": report.changed,
                        "added": report.added,
                        "removed": report.removed,
                    }
        except Exception:
            # Best-effort -- a transient IO error mid-burst (a file
            # mid-write, cache.json briefly missing) shouldn't crash the
            # watcher's background thread or take the whole watch down;
            # the next event (or the next debounce window) tries again.
            pass

    with _start_lock:
        with _watchers_lock:
            existing = _watchers.pop(key, None)
        if existing:
            existing["observer"].stop()

        ignore_spec = _build_ignore_spec(root)
        handler = _DebouncedHandler(root, ignore_spec, recompute)
        observer = Observer()
        observer.schedule(handler, str(root), recursive=True)
        observer.start()

        with _watchers_lock:
            _watchers[key] = {"observer": observer, "report": None, "started_at": time.time()}
            _evict_old_watchers(key)

    recompute()


def get_status(project_path: str) -> dict | None:
    """The watcher's latest cached freshness report for project_path, or
    None if nothing is watching it (never started, or evicted past
    MAX_WATCHERS). Callers should treat None as "no live status available"
    rather than "definitely fresh" -- this is a cache of check_freshness(),
    not a replacement for calling it directly.
    """
    with _watchers_lock:
        entry = _watchers.get(_abs_key(project_path))
    return entry["report"] if entry else None


def stop_watch(project_path: str) -> None:
    """Not currently called by any route -- start_watch()'s own
    replace-on-restart plus MAX_WATCHERS eviction already bound resource
    growth without needing the frontend to explicitly signal "navigating
    away." Kept as a public function for tests and any future caller that
    wants to free a watcher immediately rather than waiting on eviction.
    """
    key = _abs_key(project_path)
    with _watchers_lock:
        existing = _watchers.pop(key, None)
    if existing:
        existing["observer"].stop()
