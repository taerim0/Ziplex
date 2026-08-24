// The hash router + page bootstrap -- see app.js's header comment for the
// overall module split. Entry point: index.html loads only this file
// (`<script type="module" src="js/router.js">`) -- every other module gets
// pulled in transitively via `import`, so load order is now whatever the
// import graph resolves rather than something index.html has to get right
// by listing <script> tags in the correct sequence.
// ---- router -----------------------------------------------------------

import { getAif, setActiveNav, setActiveTopbar, stopStaleWatch } from "./app.js";
import { applyStaticI18n } from "./i18n.js";
import { renderPackJob } from "./pack.js";
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

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", () => {
  applyStaticI18n(); // index.html's own static topbar/sidebar labels (i18n.js) -- route() below never touches them
  route();
});
