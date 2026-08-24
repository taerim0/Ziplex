// Ziplex GUI frontend, real ES modules (native browser support via
// `<script type="module">` in index.html -- still no bundler, still no
// build step) instead of the plain-global/load-order-dependent split this
// used to be. Each file now says exactly what it needs via `import` and
// exactly what it offers via `export`, instead of every function being an
// implicit global any other file could reach into, in any load order that
// happened to put it first.
//
// This file: localStorage-backed state (aif_path/project_path/recent-
// projects list), the shared `app`/`nav`/`topbar`/`staleBadge` DOM
// element references, the api()/apiPost() fetch wrappers, the el()-
// adjacent DOM-builder helpers (el, copyButton, showError, showLoading),
// the native-picker button family (browseButton and friends, backed by
// window.pywebview.api -- see gui_server.py's _Api), and the small
// confidenceLevel()/setStale()/setActiveNav()/setActiveTopbar() display
// helpers used across every page. The lowest-level shared module --
// everything else in js/ imports from this one.

import { t } from "./i18n.js";

const LS_AIF = "ziplex.aif_path";
const LS_PROJECT = "ziplex.project_path";
const LS_RECENT = "ziplex.recent"; // JSON array of {aif, project, openedAt}, most recent first
const RECENT_MAX = 8;

export const app = document.getElementById("app");
export const nav = document.getElementById("nav");
export const topbar = document.getElementById("topbar");
export const staleBadge = document.getElementById("stale-badge");

export function getAif() { return localStorage.getItem(LS_AIF) || ""; }
export function getProject() { return localStorage.getItem(LS_PROJECT) || ""; }

// "최근 프로젝트" on the check page (Nielsen's "recognition rather than
// recall" -- a returning user shouldn't have to re-type or re-browse-to a
// path they've already opened once). Keyed by aif_path since that's the
// one required field; project_path travels alongside it for the freshness
// check but isn't itself unique. Best-effort: a private window or a
// browser with site data blocked can throw on either read or write here,
// and an empty/broken list just means "no recents shown", never a crash.
export function getRecent() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_RECENT) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch { return []; }
}

function pushRecent(aif, project) {
  if (!aif) return;
  try {
    const list = getRecent().filter(r => r.aif !== aif);
    list.unshift({ aif, project: project || "", openedAt: Date.now() });
    localStorage.setItem(LS_RECENT, JSON.stringify(list.slice(0, RECENT_MAX)));
  } catch { /* storage unavailable -- recent list just stays empty next time */ }
}

export function removeRecent(aif) {
  try {
    localStorage.setItem(LS_RECENT, JSON.stringify(getRecent().filter(r => r.aif !== aif)));
  } catch { /* best-effort, see pushRecent */ }
}

export function openProject(aif, project) {
  localStorage.setItem(LS_AIF, aif);
  localStorage.setItem(LS_PROJECT, project || "");
  pushRecent(aif, project);
  location.hash = "#/overview";
}

export function relativeTime(ms) {
  const mins = Math.round((Date.now() - ms) / 60000);
  if (mins < 1) return t("core.time.justNow");
  if (mins < 60) return t("core.time.minutesAgo", { mins });
  const hours = Math.round(mins / 60);
  if (hours < 24) return t("core.time.hoursAgo", { hours });
  return t("core.time.daysAgo", { days: Math.round(hours / 24) });
}

export async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") url.searchParams.set(k, v);
  }
  const res = await fetch(url);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || t("core.requestFailed", { status: res.status }));
  return data;
}

export async function apiPost(path, body = {}) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || t("core.requestFailed", { status: res.status }));
  return data;
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) if (c) node.appendChild(c);
  return node;
}

export function copyButton(getText, label = t("core.copy")) {
  const btn = el("button", { class: "secondary", text: label });
  btn.addEventListener("click", async () => {
    await navigator.clipboard.writeText(getText());
    btn.textContent = t("core.copied");
    setTimeout(() => (btn.textContent = label), 1200);
  });
  return btn;
}

export function showError(err) {
  app.innerHTML = "";
  app.appendChild(el("div", { class: "error", text: String(err.message || err) }));
}

// Visibility of system status (Nielsen heuristic #1): fetches to /api/* are
// local but not instant, and an empty <main> while one is in flight reads
// as "nothing happened" rather than "working on it". Callers clear this
// themselves (another app.innerHTML = "") once real content is ready to
// render -- same pattern renderSearch's inline "검색 중..." already used,
// just factored out so every page-level fetch gets it, not just search.
export function showLoading() {
  app.innerHTML = "";
  app.appendChild(el("p", { class: "muted loading", text: t("core.loading") }));
}

// pywebview injects window.pywebview.api once the native window is created
// with js_api=... (see gui_server.py's main()) -- absent in --no-window
// mode (plain browser tab), where there's no bridge to a native dialog at
// all, so every browse button below just tells a human to type the path
// instead when the bridge (or this specific method on it) isn't there.
function hasApi(method) {
  return !!(window.pywebview && window.pywebview.api && window.pywebview.api[method]);
}

// Shared by the folder/open-file/save-file pickers below -- each just picks
// a different js_api method (see gui_server.py's _Api) and label/message.
function pickerButton(targetInput, apiMethod, label, unavailableMessage) {
  const btn = el("button", { class: "secondary", text: label });
  btn.addEventListener("click", async () => {
    if (!hasApi(apiMethod)) {
      alert(unavailableMessage);
      return;
    }
    const picked = await window.pywebview.api[apiMethod]();
    if (picked) targetInput.value = picked;
  });
  return btn;
}

export function browseButton(targetInput) {
  return pickerButton(targetInput, "choose_folder", t("core.picker.browseFolder"), t("core.picker.unavailable"));
}

// aif.json 경로: an existing file to open, so this is an OPEN dialog
// (see gui_server.py's choose_aif_file), filtered to .json.
export function browseAifButton(targetInput) {
  return pickerButton(targetInput, "choose_aif_file", t("core.picker.browseFile"), t("core.picker.unavailable"));
}

// 출력 경로: a file that doesn't necessarily exist yet -- pack's own
// save_aif() will create it -- so this is a SAVE dialog, not OPEN
// (see gui_server.py's choose_save_file).
export function browseSaveButton(targetInput) {
  return pickerButton(targetInput, "choose_save_file", t("core.picker.browseFile"), t("core.picker.unavailable"));
}

export function confidenceLevel(conf) {
  return conf >= 0.67 ? "high" : conf >= 0.34 ? "medium" : "low";
}

export function setStale(stale) {
  if (stale && stale.is_stale) {
    const parts = [];
    if (stale.changed?.length) parts.push(t("core.stale.changed", { n: stale.changed.length }));
    if (stale.added?.length) parts.push(t("core.stale.added", { n: stale.added.length }));
    if (stale.removed?.length) parts.push(t("core.stale.removed", { n: stale.removed.length }));
    staleBadge.title = parts.join(", ") || t("core.stale.detected");
    staleBadge.classList.remove("hidden");
  } else {
    staleBadge.classList.add("hidden");
  }
}

// Live staleness (watcher.py, /api/watch/start + /api/watch/status) --
// makes the badge above update on its own while a project page stays
// open, instead of only ever being checked once at page-load time.
// router.js's route() calls stopStaleWatch() unconditionally at the top
// of every navigation (not just when leaving Overview/Files) so the
// interval below can never outlive the page that started it -- this hash
// router has no per-page "unmount" hook of its own, so the router itself
// is the one place every navigation is guaranteed to pass through.
let _staleWatchInterval = null;

export function startStaleWatch(projectPath, aifPath, intervalMs = 3000) {
  stopStaleWatch();
  // Both required -- a falsy projectPath means no folder on disk to watch
  // (a project opened by aif.json path alone); a falsy aifPath shouldn't
  // actually reach here (route()'s no-project-loaded guard already bounces
  // away before renderOverview()/renderFiles() would ever call this
  // without one), but checking it here too means this function is safe to
  // call on its own, from a future caller, without silently starting a
  // setInterval that polls forever for a watch /api/watch/start's own
  // validation was always going to 400 on.
  if (!projectPath || !aifPath) return;
  apiPost("/api/watch/start", { project_path: projectPath, aif_path: aifPath }).catch(() => {});
  _staleWatchInterval = setInterval(async () => {
    try {
      const data = await api("/api/watch/status", { project_path: projectPath });
      // null report (not yet computed, or the watcher got evicted) leaves
      // whatever the page's own initial "_stale" field already showed
      // alone, rather than forcing the badge to hide.
      if (data.report) setStale(data.report);
    } catch (e) { /* best-effort -- a dropped poll just tries again next tick */ }
  }, intervalMs);
}

export function stopStaleWatch() {
  if (_staleWatchInterval) {
    clearInterval(_staleWatchInterval);
    _staleWatchInterval = null;
  }
}

// Highlights the current section in the sidebar (see index.html's
// data-route attributes) -- a sidebar needs a clear "you are here"
// indicator the way the old top nav-bar's plain hyperlink list never did.
// Called once from router.js's route() per navigation, not from each
// individual render*() -- keeping "which section is this route" in one
// place instead of every page needing to know its own nav entry.
export function setActiveNav(routeName) {
  for (const a of nav.querySelectorAll("a[data-route]")) {
    a.classList.toggle("active", a.dataset.route === routeName);
  }
}

// Same idea as setActiveNav() above, one level up: the topbar's four links
// (the brand/logo included -- it's a real link now, to "/") are global
// destinations (home, start/resume a pack, open an existing one, options)
// rather than project sections, so they're highlighted independently of
// the sidebar -- none of them is active while browsing an already-loaded
// project (Overview/Files/...), since that's the sidebar's own territory,
// not this bar's. name=null (browsing pages, or any route this bar
// doesn't own) clears all four rather than leaving a stale one lit.
export function setActiveTopbar(name) {
  for (const a of topbar.querySelectorAll("a[data-topbar]")) {
    a.classList.toggle("active", a.dataset.topbar === name);
  }
}
