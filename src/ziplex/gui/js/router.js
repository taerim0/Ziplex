// The hash router + page bootstrap -- see app.js's header comment for the
// overall module split. Entry point: index.html loads only this file
// (`<script type="module" src="js/router.js">`) -- every other module gets
// pulled in transitively via `import`, so load order is now whatever the
// import graph resolves rather than something index.html has to get right
// by listing <script> tags in the correct sequence.
// ---- router -----------------------------------------------------------

import { getAif, setActiveNav, setActiveTopbar, stopStaleWatch } from "./app.js";
import { applyStaticI18n } from "./i18n.js";
import { renderPackJob, hasActiveGuard, confirmLeaveActivePackJob } from "./pack.js";
import { renderHome, renderPackHome, renderCheck } from "./pages/landing.js";
import { renderOptions } from "./pages/options.js";
import { renderOverview } from "./pages/overview.js";
import { renderFiles, renderFileDetail } from "./pages/files.js";
import { renderRelationships } from "./pages/relationships.js";
import { renderSearch } from "./pages/search.js";

export function route() {
  // Unconditional, not just on leaving Overview/Files -- this hash router
  // has no per-page "unmount" hook, so route() itself is the one place
  // every navigation is guaranteed to pass through. A no-op if nothing was
  // watching (stopStaleWatch() itself checks before clearing).
  stopStaleWatch();

  const raw = location.hash.slice(1) || "/";
  const [path, queryStr] = raw.split("?");
  const params = new URLSearchParams(queryStr || "");
  const segments = path.split("/").filter(Boolean);

  if (segments[0] === "pack" && segments.length === 2) { setActiveTopbar("pack"); return renderPackJob(segments[1]); }

  // Global destinations the topbar owns -- reachable with or without a
  // project loaded, unlike everything below, which is either a section of
  // an already-loaded project or the "nothing recognized" fallback at the
  // bottom. Checked before the aif-required guard just below for that
  // reason: "no project loaded yet" should never bounce a request for
  // Home/Pack/Check/Options back to some other one of these four.
  if (segments.length === 0) { setActiveTopbar("home"); return renderHome(); }
  if (segments[0] === "pack" && segments.length === 1) { setActiveTopbar("pack"); return renderPackHome(); }
  if (segments[0] === "check") { setActiveTopbar("check"); return renderCheck(); }
  if (segments[0] === "options") { setActiveTopbar("options"); return renderOptions(); }

  if (!getAif() && segments[0] !== undefined && segments.length) {
    // no project loaded yet -- bounce to the open-a-project screen (not the
    // pack-new-project one) since hitting e.g. #/overview directly implies
    // wanting to view something already packed, not start a fresh pack
    location.hash = "#/check";
    return;
  }

  if (segments[0] === "overview") { setActiveTopbar(null); setActiveNav("overview"); return renderOverview(); }
  if (segments[0] === "files" && segments.length === 1) { setActiveTopbar(null); setActiveNav("files"); return renderFiles(); }
  if (segments[0] === "files" && segments.length >= 2) {
    setActiveTopbar(null);
    setActiveNav("files"); // a file's own detail page still belongs to the Files section
    return renderFileDetail(decodeURIComponent(segments.slice(1).join("/")), params);
  }
  if (segments[0] === "search") { setActiveTopbar(null); setActiveNav("search"); return renderSearch(); }
  if (segments[0] === "relationships") { setActiveTopbar(null); setActiveNav("relationships"); return renderRelationships(); }
  // Unrecognized route past this point only reaches here with a project
  // already loaded (the guard above already caught the no-project case) --
  // overview is the most sensible fallback then, not one of the topbar's
  // own screens.
  location.hash = "#/overview";
}

// Blocks an in-app hash-link click (topbar, or the sidebar once a project
// is loaded) while a pack job's "running"/"reviewing" screen wants a
// chance to warn first -- see confirmLeaveActivePackJob()'s own comment in
// pack.js for why this exists (reported directly: clicking a topbar item
// mid-pack used to swap the page with zero warning). Delegated on
// `document` (capture phase, so it also runs before an anchor's own click
// handler, though none of these links have one) rather than attached per
// link, since the topbar/sidebar markup is static index.html, not something
// route() re-renders. A click already targeting the current hash (the same
// job's own URL, or any link matched to where we already are) is let
// through untouched -- nothing to confirm about staying put. hasActiveGuard()
// is checked first and synchronously so an ordinary click (no guard active,
// the common case) never even calls into the async
// confirmLeaveActivePackJob() below. When a guard *is* active, the click is
// always prevented immediately (confirmLeaveActivePackJob()'s
// showConfirmModal() is a real DOM dialog, not a blocking native one, so
// there's no synchronous answer to decide on yet) and the navigation is
// completed by hand afterward only if the human confirms leaving.
document.addEventListener("click", (e) => {
  const link = e.target.closest('a[href^="#"]');
  if (!link) return;
  const targetHash = link.getAttribute("href");
  if (targetHash === (location.hash || "#/")) return;
  if (!hasActiveGuard()) return;
  e.preventDefault();
  confirmLeaveActivePackJob().then((proceed) => {
    if (proceed) location.hash = targetHash;
  });
}, true);

// Belt-and-suspenders alongside the click-delegation above: an anchor click
// is the *reported* way to bypass the guard, but it's not the only way the
// hash can change -- the browser's own Back/Forward buttons fire
// `hashchange` directly with no click event at all, which the delegation
// above never sees (a real gap code review caught: pressing Back out of a
// "reviewing" screen silently discarded it with zero warning, exactly what
// this whole feature exists to prevent). Unlike a click, a hashchange can't
// be preventDefault()'d -- location.hash has already changed by the time
// this fires -- so an active guard reverts it back to `lastHash`
// immediately (pausing the visible navigation while the modal is shown,
// since the page underneath is still whatever `lastHash` was already
// rendering) rather than deciding first, and that revert's own hashchange
// re-enters this same function with newHash === lastHash.
//
// That re-entry -- and the settling one after a *confirmed* leave
// reassigns `location.hash` back to the original target -- has to be a
// pure no-op, not "harmlessly" call route() again: renderPackJob() tears
// down and rebuilds its whole DOM/state on every call (a fresh
// /api/pack/review fetch included), so re-invoking it here would silently
// wipe out a "reviewing" screen's own in-progress name/guide/rules/summary
// edits on every declined Back-navigation attempt -- a real regression an
// earlier version of this function had (it called route() unconditionally
// whenever newHash === lastHash, exactly this case) that went unnoticed
// because testing it only checked the final hash value, never whether the
// page underneath survived intact. If the human confirms leaving, the
// guard has already cleared itself (every guard closure does) by the time
// that reassignment's hashchange arrives, so it takes the "no active
// guard" branch below and renders normally -- once, not twice.
let lastHash = location.hash || "#/";

function guardedRoute() {
  const newHash = location.hash || "#/";
  if (newHash === lastHash) return; // our own revert (or its settling) -- nothing actually changed
  if (!hasActiveGuard()) {
    lastHash = newHash;
    route();
    return;
  }
  location.hash = lastHash;
  confirmLeaveActivePackJob().then((proceed) => {
    if (proceed) location.hash = newHash;
  });
}

window.addEventListener("hashchange", guardedRoute);
window.addEventListener("DOMContentLoaded", () => {
  applyStaticI18n(); // index.html's own static topbar/sidebar labels (i18n.js) -- route() below never touches them
  route();
});
