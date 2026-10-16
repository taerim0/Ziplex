# src/ziplex/gui/ — local read-only viewer, Flask + pywebview

Scoped notes for `gui_server.py`, `watcher.py`, and the static frontend (`index.html`, the `js/` ES-module tree, `style.css`). The GUI browses an already-packed project; it never packs, summarizes, or edits. Packing happens in a terminal (`ziplex pack <project>`, `ziplex index` for the graph) — the Home page says so. History: `docs/HISTORY.md`.

`ziplex.gui` is a subpackage: parent modules are reached via `from .. import query_service`, siblings via `from . import watcher`. Run via the `ziplex-gui` console script or `python -m ziplex.gui.gui_server`.

**Layering**: importing `gui_server` must not load `llm`/`summarizer`/`checkpoint`/`confidence`/`corrector`/`edits`/`folder_summary`/`packager`/`progress_i18n`/`enrich` (`tests/gui/test_gui_layering.py`, fresh-subprocess `sys.modules` check). Its only `ziplex` imports are `query_service`, `settings`, `watcher` (→ `freshness`, `file.collector`).

## gui_server.py

Every `/api/*` read route is a thin adapter over `query_service.py` (same core as `mcp_server.py`): `/api/overview`, `/api/files` (`folder` filter), `/api/folders`, `/api/relationships` (full shape, `include_text_refs=True`), `/api/dependents` + `/api/blast_radius` (shared `_graph_query()`, `include_text_refs` param, unknown file → 404), `/api/detail` (line range, unknown file → 404), `/api/freshness`, `/api/search` (no result cap, optional `aif_path` for the pack's own scope; bad regex → 400), `/api/config` (`--aif`/`--project` prefill + version). App-level `OSError` → 404 and `JSONDecodeError` → 400 handlers turn a typo'd path into a JSON error.

The only write routes: `/api/settings` (GET/POST over `settings.py`'s `load_settings()`/`save_settings()`; POST merges whichever `EDITABLE_FIELDS` the body carries) and `/api/watch/start`. A test pins exactly these two (`test_only_settings_and_watch_start_accept_writes`).

Binds to `127.0.0.1` only, no `--host` flag — exposing it would open local source (via search) to the network. `--no-window` opens a browser tab instead of pywebview. The windowed branch exposes module-level `_Api(webview_module)` as `window.pywebview.api` (`choose_folder()`, `choose_aif_file()`); without it the frontend asks for a typed path. `_find_free_port()` picks the next free port so a taken one can't leave the window blank.

## watcher.py

Backs `/api/watch/start`/`/api/watch/status`: live freshness for the Overview/Files badge via `watchdog`'s `Observer` (OS notifications, no polling). `start_watch(project_path, aif_path)` is idempotent and runs `recompute()` once synchronously; `MAX_WATCHERS` (5) evicts the oldest. Events matching the same ignore spec `collect_files()` uses are dropped, the rest debounced (`DEBOUNCE_SECONDS`) into one `check_freshness_scoped()` recompute. `/api/watch/start` checks the sibling `cache.json` up front since `start_watch()` itself never raises.

## Frontend (`index.html` / `js/*.js` / `style.css`)

No build step; real ES modules, `index.html` loads only `js/router.js`. State (`aif_path`/`project_path`/recent list) lives in `localStorage`; every route is stateless.

**Modules**: `js/i18n.js` (ko/en chrome dictionary, `t()`, `applyStaticI18n()`; no imports), `js/theme.js`, `js/app.js` (state, `api`/`apiPost`, `el`/`copyButton`/`showLoading`, picker buttons, freshness badge + `startStaleWatch()`/`stopStaleWatch()`, nav highlighting, `initTopbarToggles(route)`), `js/graph.js` (read-only graph components), `js/pages/` (one file per destination), `js/router.js` (`route()`, `hashchange`/`DOMContentLoaded` bootstrap).

**Pages**: topbar — Home (`#/`, tagline + "run `ziplex pack`, then open the aif.json here" hint), Check Project (`#/check`, open form + recent projects with freshness badges), Options (`#/options`, LLM provider/model/key that CLI `ziplex pack` reads via `settings.py`; no LLM-module import, defaults are plain strings). Sidebar, once a project is loaded (otherwise `route()` bounces to `#/check`): Overview (project/AI guide/tech stack/rules/tokens), Files (folder tree with folder summaries, substring filter, summary-visibility toggle) and file detail (summary, dependents/blast radius with a text-refs toggle, compressed body, Copy), Relationships, Search. Any `confidence` key in aif.json is ignored.

**Async renders** capture `navigationToken()` before a fetch and bail if `isCurrentNavigation()` is false afterward, so a slow response can't paint over the page the user moved to. `route()` calls `stopStaleWatch()` first on every navigation (no per-page unmount hook).

**Relationships** (`pages/relationships.js` + `graph.js`): `renderDependencyTreeOverview(tree, allFiles, onSelectFile)` — roots-first collapsible tree; `ancestors` (current path) flags a true cycle (🔁 + banner), a shared `seen` set renders each file's subtree once (later references are shallow "already shown" rows), bounding rendered nodes to the graph's size. Clicking a file opens `renderRelationshipViewer(tree, allFiles, selected)`: searchable file list + the selected file's `renderMiniGraph()` ego-graph (≤`REL_GRAPH_MAX_NEIGHBORS` per side, basename labels, `parentDir/basename` on collision) and edge lists; clicking a neighbor or edge moves the selection, the file name links to its detail page.

**Freshness badges** (the sidebar `#stale-badge` and each recent-project row) are real `<button>`s toggling a `renderStaleDetail()` panel (capped at `STALE_DETAIL_CAP`, "+N more"); `staleTooltip()` is a hover extra. Recent-row badge/detail clicks `stopPropagation()` so they don't open the project.

**Topbar toggles** (`#lang-toggle`/`#theme-toggle`, static markup): language re-runs `applyStaticI18n()` + `route()`; theme just flips `data-theme`. Inline scripts in `index.html` apply the saved theme/pill state before first paint.
