// Hash router + page bootstrap. index.html loads only this module; every
// other module comes in through `import`.

import { api, getAif, setActiveNav, setActiveTopbar, stopStaleWatch, initTopbarToggles, beginNavigation } from "./app.js";
import { applyStaticI18n } from "./i18n.js";
import { renderHome, renderCheck } from "./pages/landing.js";
import { renderOptions } from "./pages/options.js";
import { renderOverview } from "./pages/overview.js";
import { renderFiles, renderFileDetail } from "./pages/files.js";
import { renderRelationships } from "./pages/relationships.js";
import { renderSearch } from "./pages/search.js";

export function route() {
  // No per-page unmount hook, so every navigation stops the live watcher.
  stopStaleWatch();
  beginNavigation();

  const raw = location.hash.slice(1) || "/";
  const [path, queryStr] = raw.split("?");
  const params = new URLSearchParams(queryStr || "");
  const segments = path.split("/").filter(Boolean);

  // Topbar destinations: reachable with or without a loaded project.
  if (segments.length === 0) { setActiveTopbar("home"); return renderHome(); }
  if (segments[0] === "check") { setActiveTopbar("check"); return renderCheck(); }
  if (segments[0] === "options") { setActiveTopbar("options"); return renderOptions(); }

  if (!getAif()) {
    location.hash = "#/check";
    return;
  }

  setActiveTopbar(null);
  if (segments[0] === "overview") { setActiveNav("overview"); return renderOverview(); }
  if (segments[0] === "files" && segments.length === 1) { setActiveNav("files"); return renderFiles(); }
  if (segments[0] === "files") {
    setActiveNav("files");
    return renderFileDetail(decodeURIComponent(segments.slice(1).join("/")), params);
  }
  if (segments[0] === "search") { setActiveNav("search"); return renderSearch(); }
  if (segments[0] === "relationships") { setActiveNav("relationships"); return renderRelationships(); }
  location.hash = "#/overview";
}

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", () => {
  applyStaticI18n(); // static topbar/sidebar labels
  initTopbarToggles(route);
  route();
  // Topbar version footnote; best-effort.
  api("/api/config").then(cfg => {
    if (cfg.version) document.getElementById("app-version").textContent = `v${cfg.version}`;
  }).catch(() => {});
});
