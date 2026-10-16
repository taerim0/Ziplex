// The sidebar's Relationships section: a read-only whole-project
// dependency tree, and a per-file ego-graph view reached by clicking a file.

import { app, nav, el, api, getAif, showError, showLoading, navigationToken, isCurrentNavigation } from "../app.js";
import { t } from "../i18n.js";
import { renderDependencyTreeOverview, renderRelationshipViewer } from "../graph.js";

export async function renderRelationships() {
  nav.classList.remove("hidden");
  showLoading();
  const navToken = navigationToken();
  try {
    const tree = await api("/api/relationships", { aif_path: getAif() });
    if (!isCurrentNavigation(navToken)) return;
    const allFileNames = Object.keys(tree).sort();
    const section = el("div", {});

    function showTreeOverview() {
      section.innerHTML = "";
      section.appendChild(renderDependencyTreeOverview(tree, allFileNames, showFileView));
    }

    function showFileView(selectedFile) {
      section.innerHTML = "";
      section.appendChild(el("button", { class: "secondary", text: t("relationships.backToTree"), onclick: showTreeOverview }));
      section.appendChild(renderRelationshipViewer(tree, allFileNames, selectedFile));
    }

    showTreeOverview();

    app.innerHTML = "";
    app.appendChild(el("div", { class: "card" }, [
      el("h1", { text: t("relationships.title") }),
      el("p", { class: "muted", text: t("relationships.help") }),
      section,
    ]));
  } catch (e) { if (isCurrentNavigation(navToken)) showError(e); }
}
