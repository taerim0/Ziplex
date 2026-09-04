"""Ziplex GUI: a local browse/search companion over an already-packed
project, for environments where Claude Code (or MCP generally) isn't
available but a browser-based AI chat is -- see the `ziplex-roadmap` memory
for the full "who is this for" reasoning.

Most of the /api/* routes below are thin adapters over query_service.py
(query params in, JSON out), same core as mcp_server.py, nothing here beyond
that translation -- and still read-only, same as the MCP server. The
exception is the /api/select_files, /api/pack*, and /api/pack/* family:
those are a *write* path, running packager.pack() itself (via
pack_service.py) so a project can be packed from the GUI directly rather
than requiring a prior CLI run. That flow is interactive by default, same as
the CLI's plain `pack <path>` (no --auto, no --auto-correct): a file
selection screen before the job starts, and a correction/review screen
(project name, AI guide, rules, per-file summaries) before anything is
saved -- see pack_service.py's module docstring for the full route-by-route
mapping onto that terminal flow. Everything else stays read-only: a human
uses those pages to look around an already-packed project and copy what's
useful (each page has a Copy button) into a separate web chat by hand -- see
the roadmap memory's "selective file delivery" framing for why that
hand-off is deliberate rather than something this GUI automates.

Runs as a local Flask server wrapped in a native window via pywebview --
not a browser tab, no URL bar -- but the two are decoupled: the Flask app
underneath (`app`) also works standalone via `flask run` or a bare
`app.run()` for anyone who'd rather use their own browser (or during
development, where webview's window can make debugging fiddlier than a
normal browser tab). That decoupling has one real cost: main()'s windowed
branch exposes a `_Api` bridge (js_api=...) so the frontend's path fields --
project folder, aif.json, pack output -- get a real OS file/folder-picker
dialog instead of requiring a typed path. pywebview injects that bridge as
`window.pywebview.api` only in the native window, so it's simply absent in
`--no-window`/bare-`flask run` mode, and the frontend's picker buttons (see
js/app.js's hasApi()) fall back to asking for a typed path there rather
than assuming the bridge exists.

Binds to 127.0.0.1 only -- there is no --host flag, on purpose. Exposing
this to the network would open local project data (including original
source, via search_project) to anyone who can reach the port; the
`ziplex-roadmap` memory already rejected the equivalent idea (tunneling the
MCP server to the internet) for the same reason.

Run directly (after `pip install -e .`):
    ziplex-gui [--aif PATH] [--project PATH] [--port 5321]
    python -m ziplex.gui.gui_server [--aif PATH] [--project PATH] [--port 5321]
"""

import argparse
import json
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request

from . import pack_service
from . import watcher
from .. import query_service
from .. import settings as app_settings
from .. import __version__
from ..file.relationship import CycleError
from ..llm import LANGUAGE_NAMES

# static_folder="." -- not "gui" -- since index.html/the js/ module tree/
# style.css are this file's own siblings now that gui_server.py itself
# lives in src/gui/. Flask's static route handles the nested js/pages/
# path the same as a top-level file, so no extra config is needed for it.
app = Flask(__name__, static_folder=".", static_url_path="")

# Filled in from CLI args at startup (see main()); read by GET /api/config
# so the frontend can prefill the landing page without a templating layer --
# index.html/the js/ module tree stay plain static files this way. "version"
# is the one entry never overwritten by main() -- it's the installed
# package's own __version__, read once at import time and shown in the
# topbar (see router.js's bootstrap) so a human looking at the GUI can tell
# which build they're running without a separate `ziplex --version` call.
_default_config = {"aif_path": None, "project_path": None, "version": __version__}


# query_service's functions open aif_path/project_path straight off disk
# with no validation -- a typo'd path on the landing page is the single most
# likely failure mode this GUI has. Only /api/detail and /api/search had
# their own try/except (for get_detail's/search_project's ValueError);
# without these two handlers, a bad path anywhere else fell through as
# Flask's default 500 HTML page, which js/app.js's api() error-message
# handling can't extract anything useful from ("요청 실패 (500)" with no
# reason). Registered
# once here instead of adding try/except to every route.
@app.errorhandler(OSError)
def handle_os_error(e):
    return jsonify({"error": f"파일을 열 수 없습니다: {e.filename or e.strerror or e}"}), 404


@app.errorhandler(json.JSONDecodeError)
def handle_bad_json(e):
    return jsonify({"error": f"JSON 파싱 실패: {e}"}), 400


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/config")
def api_config():
    return jsonify(_default_config)



@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    """GET returns settings.py's whole persisted shape (see that module's
    own docstring for what each field means). POST accepts a partial body
    -- whichever of settings.EDITABLE_FIELDS the caller actually sends --
    merged onto whatever was already saved rather than requiring the whole
    shape back, so a caller that only knows about one field (or one
    provider's fields) can't accidentally wipe another out.
    `project_output_dirs` is deliberately not in that whitelist: no request
    body here ever includes it at all -- a folder pin is set implicitly by
    packing with an explicit output path (pack_service.start_pack_job()),
    never through this route. `ziplex settings set` (cli.py) is the same
    whitelist's other caller.
    """
    if request.method == "GET":
        return jsonify(app_settings.load_settings())

    data = request.get_json(silent=True) or {}
    current = app_settings.load_settings()
    for field in app_settings.EDITABLE_FIELDS:
        if field in data:
            current[field] = (data.get(field) or "").strip()
    app_settings.save_settings(current)
    return jsonify(current)


def _project_dir_error(project_path: str):
    """None if project_path is a real directory, else the (jsonify, status)
    tuple a route should return as-is -- shared by every route that takes a
    project_path directly (as opposed to an aif_path already validated by
    query_service's own OSError handling), so the same typo'd-path message
    doesn't need re-typing at each call site.
    """
    if not Path(project_path).is_dir():
        return jsonify({"error": f"프로젝트 폴더를 찾을 수 없습니다: {project_path}"}), 404
    return None


def _cache_path_error(aif_path: str):
    """None if aif_path's sibling cache.json exists, else a (jsonify,
    status) tuple to return as-is. Specifically for /api/watch/start --
    every *other* route taking an aif_path relies on query_service's own
    OSError handling to catch a bad path, but watcher.start_watch() never
    raises (a missing cache.json is swallowed inside its own recompute(),
    same as any other transient IO error mid-burst -- see that function's
    own comment on why), so without this a typo'd/missing aif_path here
    would silently report {"ok": true} and leave the watch permanently
    stuck on a null report instead of surfacing anything to the caller.
    """
    cache_path = Path(aif_path).with_name(f"{Path(aif_path).stem}.cache.json")
    if not cache_path.exists():
        return jsonify({"error": f"cache.json을 찾을 수 없습니다: {cache_path}"}), 404
    return None


@app.route("/api/select_files")
def api_select_files():
    """The read-only step before a pack job exists: collect + security-scan
    project_path and return the safe/dangerous split as relative names, so
    the GUI can show a human a checklist -- the browser equivalent of
    select_files()'s terminal picker. See pack_service.list_selectable_files().
    """
    project_path = request.args["project_path"]
    error = _project_dir_error(project_path)
    if error:
        return error
    return jsonify(pack_service.list_selectable_files(project_path))


@app.route("/api/pack", methods=["POST"])
def api_pack_start():
    """Kicks off a background pack of project_path (JSON body) and returns
    {"job_id": ...} immediately -- poll /api/pack/status with it. The job
    pauses in state "reviewing" once analysis finishes, same as the CLI's
    plain (non---auto-correct) `pack`; see /api/pack/review and
    /api/pack/finalize below, and pack_service.py's module docstring for the
    full interactive-parity picture.

    Optional `no_llm` mirrors CLI `pack --no-llm` -- see
    pack_service.start_pack_job()'s docstring. Optional `lang` mirrors CLI
    `pack --lang` (llm.LANGUAGE_NAMES's keys -- "en"/"ko" as of this
    writing); an unrecognized or missing value falls back to "en", the
    default and recommended choice, same as packager.pack()'s own
    defensive fallback. Optional `resume` (default False) is set only by
    the error screen's "다시 시도" button, never by a fresh pack-form
    submission -- see start_pack_job()'s own docstring for what it changes.

    Optional `progress_lang` -- unrelated to `lang` above, see
    pack_service.start_pack_job()'s own docstring -- is the frontend's
    current display language (`js/i18n.js`'s `getLang()`, sent by
    `js/pack.js` alongside this request), not a packed-content choice;
    missing/unrecognized falls back to "ko", today's existing default.
    """
    data = request.get_json(silent=True) or {}
    project_path = (data.get("project_path") or "").strip()
    if not project_path:
        return jsonify({"error": "project_path가 필요합니다"}), 400
    error = _project_dir_error(project_path)
    if error:
        return error

    selected_files = data.get("selected_files")
    if not selected_files:
        return jsonify({"error": "선택된 파일이 없습니다"}), 400

    output_path = (data.get("output_path") or "").strip() or None
    no_cache = bool(data.get("no_cache"))
    no_llm = bool(data.get("no_llm"))
    lang = data.get("lang") if data.get("lang") in LANGUAGE_NAMES else "en"
    resume = bool(data.get("resume"))
    progress_lang = data.get("progress_lang") if data.get("progress_lang") in ("en", "ko") else "ko"
    job_id = pack_service.start_pack_job(
        project_path, output_path, no_cache=no_cache, no_llm=no_llm, selected_files=selected_files, lang=lang,
        resume=resume, progress_lang=progress_lang,
    )
    return jsonify({"job_id": job_id})


@app.route("/api/pack/review")
def api_pack_review():
    """The paused job's project/rules/per-file-summary state for a GUI
    correction screen, triaged by confidence the same way corrector.py's
    terminal flow triages it. 404 while the job is still running or once
    it's already been finalized/errored -- the review payload only exists in
    the "reviewing" window.
    """
    job_id = request.args["job_id"]
    review = pack_service.get_review(job_id)
    if review is None:
        return jsonify({"error": f"검토 가능한 작업이 아닙니다: {job_id}"}), 404
    return jsonify(review)


def _relationship_edit_route(id_field: str, result_key: str, fn):
    """Shared body for the four link/unlink routes below (job-based:
    api_pack_link/api_pack_unlink, and saved-file-based:
    api_relationships_link/api_relationships_unlink): parse
    {id_field, file, target} from the JSON body, call
    fn(id_value, file_name, target), and map CycleError/ValueError to the
    same 409/404 status every one of the four already used. Kept in one
    place instead of copied four times so a future change to that mapping
    (a new exception type, a different status code) can't be made in three
    of the four spots and missed in the fourth. remove_dependency_in_job()/
    unlink_saved_relationship() never actually raise CycleError (removing
    an edge can't create one) -- catching it for the unlink routes too is a
    harmless no-op branch, not a behavior change from before this was
    factored out.
    """
    data = request.get_json(silent=True) or {}
    id_value = data.get(id_field)
    file_name = data.get("file")
    target = data.get("target")
    if not id_value or not file_name or not target:
        return jsonify({"error": f"{id_field}, file, target가 모두 필요합니다"}), 400
    try:
        result = fn(id_value, file_name, target)
    except CycleError as e:
        return jsonify({"error": str(e)}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({result_key: result})


@app.route("/api/pack/link", methods=["POST"])
def api_pack_link():
    """Relationship-editor "link" endpoint: adds the dependency edge `file`
    -> `target` in a "reviewing" job and returns the recomputed tree. See
    pack_service.add_dependency_in_job() -- only `file`'s own edges change,
    unlike the drag-and-drop reparenting this replaced.
    """
    return _relationship_edit_route("job_id", "tree", pack_service.add_dependency_in_job)


@app.route("/api/pack/unlink", methods=["POST"])
def api_pack_unlink():
    """Relationship-editor "unlink" endpoint: removes the dependency edge
    `file` -> `target` in a "reviewing" job and returns the recomputed tree.
    See pack_service.remove_dependency_in_job().
    """
    return _relationship_edit_route("job_id", "tree", pack_service.remove_dependency_in_job)


@app.route("/api/pack/finalize", methods=["POST"])
def api_pack_finalize():
    """Applies whatever corrections the GUI submitted and saves the result --
    see pack_service.submit_review() for how each field maps onto edits.py's
    setters.
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id가 필요합니다"}), 400
    try:
        result = pack_service.submit_review(
            job_id,
            project_name=data.get("project_name"),
            project_prompt=data.get("project_prompt"),
            rules=data.get("rules"),
            summaries=data.get("summaries"),
            folder_summaries=data.get("folder_summaries"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(result)


@app.route("/api/pack/cancel", methods=["POST"])
def api_pack_cancel():
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id or not pack_service.cancel_job(job_id):
        return jsonify({"error": f"취소할 수 있는 작업이 아닙니다: {job_id}"}), 404
    return jsonify({"ok": True})


@app.route("/api/pack/stop", methods=["POST"])
def api_pack_stop():
    """The "running"-state counterpart to /api/pack/cancel above (which only
    ever applies to a paused "reviewing" job -- there's nothing running to
    stop by then). `save: true` checkpoints wherever analysis has gotten to
    (packager.pack()'s check_cancelled param, via
    pack_service.request_cancel()) so a later pack on the same project
    auto-resumes from it; `save: false` just discards it. Not instant --
    the job keeps polling as "running" until pack() reaches its next
    checkpoint and actually returns.
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id or not pack_service.request_cancel(job_id, bool(data.get("save"))):
        return jsonify({"error": f"중단할 수 있는 작업이 아닙니다: {job_id}"}), 404
    return jsonify({"ok": True})


@app.route("/api/pack/status")
def api_pack_status():
    job_id = request.args["job_id"]
    since = request.args.get("since", default=0, type=int)
    status = pack_service.get_job_status(job_id, since)
    if status is None:
        return jsonify({"error": f"알 수 없는 job_id: {job_id}"}), 404
    return jsonify(status)


@app.route("/api/overview")
def api_overview():
    aif_path = request.args["aif_path"]
    project_path = request.args.get("project_path") or None
    return jsonify(query_service.get_overview(aif_path, project_path))


@app.route("/api/files")
def api_files():
    aif_path = request.args["aif_path"]
    project_path = request.args.get("project_path") or None
    return jsonify(query_service.list_files(aif_path, project_path))


@app.route("/api/folders")
def api_folders():
    aif_path = request.args["aif_path"]
    return jsonify(query_service.get_folders(aif_path))


@app.route("/api/relationships")
def api_relationships():
    aif_path = request.args["aif_path"]
    return jsonify(query_service.get_relationships(aif_path))


@app.route("/api/relationships/link", methods=["POST"])
def api_relationships_link():
    """Post-pack counterpart to /api/pack/link: edits an already-saved
    project's relationships directly on disk (no job_id, no review screen)
    -- see pack_service.link_saved_relationship() for why this can't just
    reuse the pack/link path.
    """
    return _relationship_edit_route("aif_path", "relationships", pack_service.link_saved_relationship)


@app.route("/api/relationships/unlink", methods=["POST"])
def api_relationships_unlink():
    """Post-pack counterpart to /api/pack/unlink. See
    pack_service.unlink_saved_relationship().
    """
    return _relationship_edit_route("aif_path", "relationships", pack_service.unlink_saved_relationship)


@app.route("/api/dependents")
def api_dependents():
    aif_path = request.args["aif_path"]
    file = request.args["file"]
    return jsonify(query_service.get_dependents(aif_path, file))


@app.route("/api/blast_radius")
def api_blast_radius():
    aif_path = request.args["aif_path"]
    file = request.args["file"]
    return jsonify(query_service.get_blast_radius(aif_path, file))


@app.route("/api/detail")
def api_detail():
    aif_path = request.args["aif_path"]
    file = request.args["file"]
    start_line = request.args.get("start_line", type=int)
    end_line = request.args.get("end_line", type=int)
    try:
        compressed = query_service.get_detail(aif_path, file, start_line, end_line)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"compressed": compressed})


@app.route("/api/freshness")
def api_freshness():
    project_path = request.args["project_path"]
    aif_path = request.args["aif_path"]
    return jsonify(query_service.check_freshness(project_path, aif_path))


@app.route("/api/watch/start", methods=["POST"])
def api_watch_start():
    """Starts (or restarts) live-watching project_path in the background --
    see watcher.py's own module docstring for why this makes the Overview/
    Files staleness badge update on its own instead of only on page load.
    Fire-and-forget from the frontend's point of view: the actual status is
    read back separately via /api/watch/status, polled on an interval (see
    js/app.js's startStaleWatch()).
    """
    data = request.get_json(silent=True) or {}
    project_path = (data.get("project_path") or "").strip()
    aif_path = (data.get("aif_path") or "").strip()
    if not project_path or not aif_path:
        return jsonify({"error": "project_path와 aif_path가 필요합니다"}), 400
    error = _project_dir_error(project_path) or _cache_path_error(aif_path)
    if error:
        return error
    watcher.start_watch(project_path, aif_path)
    return jsonify({"ok": True})


@app.route("/api/watch/status")
def api_watch_status():
    """The watcher's latest cached freshness report for project_path, or
    {"report": None} if nothing's watching it yet (never started, or
    evicted -- see watcher.py's MAX_WATCHERS). Never triggers a recompute
    itself -- this only ever reads whatever the background watcher already
    has cached, so polling it on an interval costs nothing beyond the read.
    """
    project_path = request.args.get("project_path", "").strip()
    if not project_path:
        return jsonify({"error": "project_path가 필요합니다"}), 400
    return jsonify({"report": watcher.get_status(project_path)})


@app.route("/api/search")
def api_search():
    project_path = request.args["project_path"]
    pattern = request.args["pattern"]
    context_lines = request.args.get("context_lines", default=0, type=int)
    ignore_case = request.args.get("ignore_case", default="false") == "true"
    try:
        # max_results=None: unlike an MCP/agent caller, a human browsing
        # the GUI pays no per-token cost and can already scroll/refine --
        # query_service.search_project()'s own default cap exists for the
        # other transport, not this one. Only "matches" is a bare JSON
        # array here, matching this route's existing response shape --
        # "truncated" is meaningless once max_results=None.
        result = query_service.search_project(project_path, pattern, context_lines, ignore_case, max_results=None)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result["matches"])


_JSON_FILE_TYPES = ("JSON 파일 (*.json)", "모든 파일 (*.*)")


class _Api:
    """Exposed to the frontend as window.pywebview.api once the main window
    is created with js_api=... (see main()) -- lets the frontend open a real
    OS file/folder-picker dialog instead of requiring a human to type a path
    by hand, for every path field the landing page has. Only available in
    the default windowed mode: --no-window opens a plain browser tab with
    no pywebview bridge, so the frontend falls back to manual entry there
    (see js/app.js's hasApi()).

    Defined at module level (not nested inside main(), where it used to
    live) even though `webview` itself is only ever imported lazily inside
    main()'s windowed branch (not moved to a top-level import -- --no-window
    mode has no reason to require the pywebview package, or trigger
    whatever native-backend probing importing it does, at all). Takes the
    already-imported module as a constructor argument instead of assuming a
    module-level `webview` name exists, so this class stays valid to define
    (and, e.g., unit-test) independent of whether main() has run yet.
    """

    def __init__(self, webview_module):
        self._webview = webview_module

    def choose_folder(self) -> str | None:
        result = self._webview.windows[0].create_file_dialog(self._webview.FileDialog.FOLDER)
        return result[0] if result else None

    def choose_aif_file(self) -> str | None:
        """The landing page's "aif.json 경로" field -- picks an
        *existing* file, so this is an OPEN dialog. Filtered to
        .json since that's what pack ever writes, but "모든 파일"
        is still offered in case someone renamed/moved it.
        """
        result = self._webview.windows[0].create_file_dialog(
            self._webview.FileDialog.OPEN, file_types=_JSON_FILE_TYPES
        )
        return result[0] if result else None

    def choose_save_file(self) -> str | None:
        """The pack form's "출력 경로" field -- the target doesn't
        exist yet (save_aif() creates it), so this is a SAVE
        dialog, not OPEN: it lets a human pick/type a destination
        filename in a folder they browse to, same as any other
        app's "save as".
        """
        result = self._webview.windows[0].create_file_dialog(
            self._webview.FileDialog.SAVE, save_filename="project.json", file_types=_JSON_FILE_TYPES
        )
        return result[0] if result else None


def _confirm_close_if_reviewing(window) -> bool | None:
    """Registered on window.events.closing (see the pywebview source: a
    handler returning False cancels the close, anything else lets it
    proceed). js/pack.js's beforeunload guard protects an in-page reload/
    navigation during a pack review, but clicking this native window's own
    close button bypasses the DOM entirely -- pywebview tears the webview
    down directly rather than navigating it away, so beforeunload never
    fires. This is the same gap closed for --no-window/plain-browser-tab
    mode by the browser's own close-tab prompt; the native window needs its
    own guard because there's no browser chrome to provide one.

    Checks pack_service directly (has_reviewing_job()) instead of
    round-tripping into page JS via evaluate_js() -- calling that from a
    closing handler risks deadlocking against the webview's own message
    loop on some backends, while create_confirmation_dialog below is the
    same safe pattern pywebview's own built-in confirm_close option uses
    internally (a native modal, not a JS call).

    Takes `window` as a parameter (not closed over) so it can be registered
    the same way _Api is used -- module-level, not nested inside main().
    """
    if not pack_service.has_reviewing_job():
        return None
    return window.create_confirmation_dialog(
        "검토 중인 작업이 있습니다",
        "저장하지 않은 패킹 검토 내용(이름/가이드/룰/요약)이 있습니다. 그래도 닫으시겠습니까?",
    )


def _find_free_port(preferred: int) -> int:
    """Returns `preferred` if nothing's listening on it yet, otherwise the
    next free port after it (checked up to 50 ports ahead).

    Without this, a port already in use fails inside app.run(), which runs
    in a background thread (see main()) -- that OSError has no way to reach
    main() before it goes on to open a pywebview window pointed at a server
    that never started, silently showing a blank/unreachable window with no
    indication why. Picking a free port up front avoids the failure
    entirely instead of trying to detect and report it after the fact.
    """
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:  # nothing answered -> free
                return port
    raise RuntimeError(f"{preferred}-{preferred + 49} 범위에 사용 가능한 포트가 없습니다")


def main():
    # Windows consoles default to the system locale's codepage (e.g. cp949 on
    # Korean Windows), not UTF-8 -- the port-fallback message below (and
    # anything else printed to the real console, as opposed to a pack job's
    # captured log) would otherwise raise UnicodeEncodeError on its first
    # emoji. Guarded with hasattr since a piped/captured stream (tests) may
    # not support reconfigure() at all.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Ziplex GUI")
    parser.add_argument("--version", action="version", version=f"ziplex-gui {__version__}")
    parser.add_argument("--aif", default=None, help="시작 시 미리 채울 aif.json 경로")
    parser.add_argument("--project", default=None, help="시작 시 미리 채울 프로젝트 폴더 경로")
    parser.add_argument("--port", type=int, default=5321)
    parser.add_argument("--no-window", action="store_true", help="pywebview 창 대신 기본 브라우저로 열기")
    args = parser.parse_args()

    _default_config["aif_path"] = args.aif
    _default_config["project_path"] = args.project

    port = _find_free_port(args.port)
    if port != args.port:
        print(f"⚠️  포트 {args.port}이(가) 사용 중이라 {port}번으로 대신 실행합니다.", flush=True)
    url = f"http://127.0.0.1:{port}/"

    def run_flask():
        app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)

    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()

    if args.no_window:
        webbrowser.open(url)
        thread.join()
    else:
        import webview

        window = webview.create_window("Ziplex", url, width=1100, height=800, js_api=_Api(webview))
        window.events.closing += lambda: _confirm_close_if_reviewing(window)
        webview.start()


if __name__ == "__main__":
    main()
