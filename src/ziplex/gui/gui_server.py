"""Ziplex GUI: a local, read-only viewer over an already-packed project.

Every /api/* route is a thin adapter over query_service.py (query params in,
JSON out) -- the same core mcp_server.py sits on. Nothing here packs,
summarizes, or writes aif.json; packing happens in a terminal
(`ziplex pack <project>`, or `ziplex index` for the graph). The one write
route is /api/settings (~/.ziplex/settings.json, via settings.py).

Runs as a Flask server wrapped in a native pywebview window by default;
`--no-window` (or a bare `app.run()`) uses a plain browser tab instead. The
windowed branch exposes `_Api` as window.pywebview.api for native
file/folder pickers; without it the frontend falls back to typed paths.

Binds to 127.0.0.1 only, on purpose: exposing this would open local project
data (including original source, via search_project) to the network.

    ziplex-gui [--aif PATH] [--project PATH] [--port 5321] [--no-window]
    python -m ziplex.gui.gui_server ...
"""

import argparse
import json
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request

from . import watcher
from .. import query_service
from .. import settings as app_settings
from .. import __version__

app = Flask(__name__, static_folder=".", static_url_path="")

# Filled in from CLI args at startup (see main()); read by GET /api/config so
# the frontend can prefill the open-project form.
_default_config = {"aif_path": None, "project_path": None, "version": __version__}


# query_service opens aif_path/project_path straight off disk -- a typo'd
# path should come back as a JSON error, not Flask's default 500 HTML page.
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
    """GET returns settings.py's persisted shape. POST merges whichever of
    settings.EDITABLE_FIELDS the body carries onto what's already saved, so
    a partial body can't wipe out other fields.
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
    """None if project_path is a directory, else a (response, status) tuple."""
    if not Path(project_path).is_dir():
        return jsonify({"error": f"프로젝트 폴더를 찾을 수 없습니다: {project_path}"}), 404
    return None


def _cache_path_error(aif_path: str):
    """None if aif_path's sibling cache.json exists, else a (response,
    status) tuple. watcher.start_watch() never raises on a missing cache, so
    /api/watch/start checks this up front.
    """
    cache_path = Path(aif_path).with_name(f"{Path(aif_path).stem}.cache.json")
    if not cache_path.exists():
        return jsonify({"error": f"cache.json을 찾을 수 없습니다: {cache_path}"}), 404
    return None


@app.route("/api/overview")
def api_overview():
    aif_path = request.args["aif_path"]
    project_path = request.args.get("project_path") or None
    return jsonify(query_service.get_overview(aif_path, project_path))


@app.route("/api/files")
def api_files():
    aif_path = request.args["aif_path"]
    project_path = request.args.get("project_path") or None
    folder = request.args.get("folder") or None
    return jsonify(query_service.list_files(aif_path, project_path, folder))


@app.route("/api/folders")
def api_folders():
    return jsonify(query_service.get_folders(request.args["aif_path"]))


@app.route("/api/relationships")
def api_relationships():
    aif_path = request.args["aif_path"]
    # getlist() returns [] when absent; that must stay None (files=[] means
    # "return nothing" to query_service).
    files = request.args.getlist("files") or None
    # The graph viewer shows prose-mention edges too, so it asks for the
    # full shape (MCP's default is certain edges only).
    return jsonify(query_service.get_relationships(aif_path, files, include_text_refs=True))


def _graph_query(fn):
    aif_path = request.args["aif_path"]
    file = request.args["file"]
    include_text_refs = request.args.get("include_text_refs", default="true") == "true"
    try:
        result = fn(aif_path, file, include_text_refs=include_text_refs)
    except ValueError as e:  # unknown file
        return jsonify({"error": str(e)}), 404
    return jsonify(result)


@app.route("/api/dependents")
def api_dependents():
    return _graph_query(query_service.get_dependents)


@app.route("/api/blast_radius")
def api_blast_radius():
    return _graph_query(query_service.get_blast_radius)


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
    """Starts (or restarts) live-watching project_path; the report is read
    back via /api/watch/status (polled by js/app.js's startStaleWatch()).
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
    """The watcher's latest cached report, or {"report": None} if nothing is
    watching project_path. Never recomputes on its own.
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
    # Optional: respects the pack's own one-off --include/--ignore scope.
    aif_path = request.args.get("aif_path", "").strip() or None
    try:
        # No result cap: a human browsing pays no per-token cost.
        result = query_service.search_project(
            project_path, pattern, context_lines, ignore_case, max_results=None, aif_path=aif_path
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result["matches"])


_JSON_FILE_TYPES = ("JSON 파일 (*.json)", "모든 파일 (*.*)")


class _Api:
    """window.pywebview.api in the native window: OS file/folder pickers.
    Takes the lazily-imported `webview` module so --no-window never imports
    pywebview at all.
    """

    def __init__(self, webview_module):
        self._webview = webview_module

    def choose_folder(self) -> str | None:
        result = self._webview.windows[0].create_file_dialog(self._webview.FileDialog.FOLDER)
        return result[0] if result else None

    def choose_aif_file(self) -> str | None:
        result = self._webview.windows[0].create_file_dialog(
            self._webview.FileDialog.OPEN, file_types=_JSON_FILE_TYPES
        )
        return result[0] if result else None


def _find_free_port(preferred: int) -> int:
    """`preferred` if free, else the next free port (up to 50 ahead) -- a
    taken port would otherwise fail silently inside the Flask thread while
    the window opens onto nothing.
    """
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"{preferred}-{preferred + 49} 범위에 사용 가능한 포트가 없습니다")


def main():
    # Windows consoles default to the locale codepage; printing an emoji
    # would raise without this.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Ziplex GUI (read-only viewer)")
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

        # Title-bar icon (Windows backend honors it); the taskbar icon comes
        # from the launcher exe's own resources and can't be set here.
        icon_path = Path(__file__).parent / "assets" / "icon.ico"
        webview.create_window("Ziplex", url, width=1100, height=800, js_api=_Api(webview))
        webview.start(icon=str(icon_path) if icon_path.is_file() else None)


if __name__ == "__main__":
    main()
