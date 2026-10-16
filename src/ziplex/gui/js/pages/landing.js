// Pre-project screens: renderHome() (route "/") and renderCheck() (route
// "/check": open an aif.json + the recent-projects list). The GUI is a
// read-only viewer -- packing happens in a terminal, so Home only explains
// how to produce an aif.json.

import {
  app, nav, el, api, getAif, getProject, getRecent, removeRecent,
  openProject, relativeTime, browseButton, browseAifButton,
  staleTooltip, renderStaleDetail,
} from "../app.js";
import { t } from "../i18n.js";

function packHint() {
  return el("div", { class: "card pack-hint" }, [
    el("h2", { text: t("home.packHintTitle") }),
    el("p", { text: t("home.packHint") }),
    el("pre", { text: "ziplex pack <project>\nziplex index <project>" }),
    el("p", { class: "muted", text: t("home.packHintThen") }),
  ]);
}

export function renderHome() {
  nav.classList.add("hidden");
  app.innerHTML = "";
  app.appendChild(el("div", { class: "landing" }, [
    el("div", { class: "card landing-intro" }, [
      el("h1", {}, [
        el("img", { src: "assets/logo.png", alt: "", class: "brand-mark brand-mark-large" }),
        el("span", { text: "Ziplex" }),
      ]),
      el("p", { text: t("home.tagline") }),
      el("div", { class: "copy-row" }, el("a", { href: "#/check", class: "button-link", text: t("nav.check") })),
    ]),
    packHint(),
  ]));
}

// Open-project form (aif.json path + optional project folder for the
// freshness check) plus the recent-projects list.
export function renderCheck() {
  nav.classList.add("hidden");
  app.innerHTML = "";

  const aifInput = el("input", { type: "text", id: "aif-input", placeholder: t("check.form.aifPlaceholder"), value: getAif() });
  const projInput = el("input", { type: "text", id: "proj-input", placeholder: t("check.form.projectPlaceholder"), value: getProject() });

  const openCard = el("div", { class: "card landing-intro" }, [
    el("h1", { text: t("nav.check") }),
    el("p", { text: t("check.form.description") }),
    el("p", { class: "muted", text: t("check.form.packHint") }),
    el("label", { text: t("check.form.aifLabel") }),
    el("div", { class: "input-row" }, [aifInput, browseAifButton(aifInput)]),
    el("label", { text: t("check.form.projectLabel") }),
    el("div", { class: "input-row" }, [projInput, browseButton(projInput)]),
    el("div", { class: "copy-row" }, [
      el("button", { text: t("check.form.open"), onclick: () => {
        const aif = aifInput.value.trim();
        if (!aif) { aifInput.focus(); return; }
        openProject(aif, projInput.value.trim());
      } }),
    ]),
  ]);

  const checkChildren = [openCard];
  const recent = getRecent();
  if (recent.length) {
    const recentList = el("div", { class: "recent-list" }, recent.map(r => {
      // Only checkable when a project folder path was recorded alongside
      // this aif -- openProject()'s second arg is optional, so an entry
      // opened by aif.json path alone has nothing on disk to diff against.
      // /api/freshness (query_service.py's check_freshness()) is a hash
      // comparison, no LLM calls -- cheap enough to fire once per row on
      // every visit to this page without asking first, unlike a full
      // re-pack. Best-effort: a moved/deleted project folder or a missing
      // cache.json just leaves the badge blank instead of breaking the row.
      //
      // Disabled by default (nothing to expand yet) -- a <button>, not a
      // <span>, so which files changed is reachable by click/keyboard too,
      // not only a hover .title (same accessibility fix as the top-of-page
      // staleBadge, see app.js's renderStaleDetail()).
      const badge = el("button", { type: "button", class: "recent-freshness muted", disabled: "" });
      // Stops its own clicks from bubbling to .recent-main's openProject()
      // below -- selecting a filename in the expanded list shouldn't also
      // open the project.
      const detail = el("div", { class: "recent-detail hidden", onclick: (e) => e.stopPropagation() });
      badge.addEventListener("click", (e) => {
        e.stopPropagation(); // don't also trigger .recent-main's openProject()
        const open = detail.classList.contains("hidden");
        detail.classList.toggle("hidden", !open);
        badge.setAttribute("aria-expanded", open ? "true" : "false");
      });
      if (r.project) {
        api("/api/freshness", { project_path: r.project, aif_path: r.aif })
          .then(report => {
            const changedCount = (report.changed?.length || 0) + (report.added?.length || 0) + (report.removed?.length || 0);
            badge.textContent = report.is_stale ? t("check.freshness.stale", { n: changedCount }) : t("check.freshness.fresh");
            badge.classList.toggle("stale", !!report.is_stale);
            // Hover for a quick count breakdown; click/Enter for the actual
            // filenames (below) -- same tooltip the top-of-page staleBadge
            // uses once a project's actually open.
            if (report.is_stale) {
              badge.title = staleTooltip(report);
              badge.removeAttribute("disabled");
              badge.setAttribute("aria-expanded", "false");
              detail.appendChild(renderStaleDetail(report));
            }
          })
          .catch(() => {}); // typo'd/moved path, missing cache.json, ... -- leave the badge blank
      }

      const row = el("div", { class: "recent-row" }, [
        el("div", { class: "recent-main", onclick: () => openProject(r.aif, r.project) }, [
          el("div", { class: "recent-aif", text: r.aif }),
          el("div", { class: "recent-meta" }, [
            el("span", { text: `${r.project ? r.project + " · " : ""}${relativeTime(r.openedAt)}` }),
            badge,
          ]),
          detail,
        ]),
        el("button", { class: "secondary recent-remove", text: "✕", onclick: (e) => {
          e.stopPropagation();
          removeRecent(r.aif);
          row.remove();
        } }),
      ]);
      return row;
    }));
    checkChildren.unshift(el("div", { class: "card recent-card" }, [el("h2", { text: t("check.recentTitle") }), recentList]));
  }
  app.appendChild(el("div", { class: "landing" }, checkChildren));

  // prefill from server-side --aif/--project if nothing saved locally yet
  if (!getAif()) {
    api("/api/config").then(cfg => {
      if (cfg.aif_path) aifInput.value = cfg.aif_path;
      if (cfg.project_path) projInput.value = cfg.project_path;
    }).catch(() => {});
  }
}
