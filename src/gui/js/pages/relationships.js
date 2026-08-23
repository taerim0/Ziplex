// Post-pack counterpart to the pack review screen's tree section (see
// pack.js's showReviewState()) -- see app.js's header comment for the
// overall module split. The exact same two components (renderDependencyTree
// Overview, renderRelationshipEditor), just sourced from an already-saved
// project's /api/relationships instead of a live job's review.tree, and
// edited via /api/relationships/link|unlink (pack_service.
// link_saved_relationship()/unlink_saved_relationship() -- no job_id, edits
// aif.json on disk directly) instead of /api/pack/link|unlink. Lets a human
// fix a relationship they notice is wrong after packing without re-running
// the whole pipeline. Low-confidence files (same 0.34 threshold
// confidenceLevel()/corrector.py's triage() use) are flagged in the tree
// the same way a review's needs_review list flags them, since "worth a
// second look" doesn't stop being true just because packing already
// finished.

import { app, nav, el, api, apiPost, getAif, showError, showLoading, confidenceLevel } from "../app.js";
import { t } from "../i18n.js";
import { renderDependencyTreeOverview, renderRelationshipEditor } from "../graph.js";

export async function renderRelationships() {
  nav.classList.remove("hidden");
  showLoading();
  const aifPath = getAif();
  try {
    const [relationships, files] = await Promise.all([
      api("/api/relationships", { aif_path: aifPath }),
      api("/api/files", { aif_path: aifPath }),
    ]);
    delete files._stale;
    const allFileNames = Object.keys(relationships).sort();
    const flaggedFileNames = allFileNames.filter(
      name => confidenceLevel(files[name]?.confidence ?? 1.0) === "low"
    );

    let currentTree = relationships;
    const section = el("div", {});
    const editError = el("div", { class: "error hidden" });

    function showTreeOverview() {
      section.innerHTML = "";
      section.appendChild(renderDependencyTreeOverview(currentTree, allFileNames, flaggedFileNames, showEditView));
    }

    function showEditView(selectedFile) {
      section.innerHTML = "";
      let relEditor;
      relEditor = renderRelationshipEditor(
        currentTree,
        allFileNames,
        async (file, target) => {
          editError.classList.add("hidden");
          try {
            const res = await apiPost("/api/relationships/link", { aif_path: aifPath, file, target });
            currentTree = res.relationships;
            relEditor.setTree(currentTree);
          } catch (e) {
            editError.textContent = e.message;
            editError.classList.remove("hidden");
          }
        },
        async (file, target) => {
          editError.classList.add("hidden");
          try {
            const res = await apiPost("/api/relationships/unlink", { aif_path: aifPath, file, target });
            currentTree = res.relationships;
            relEditor.setTree(currentTree);
          } catch (e) {
            editError.textContent = e.message;
            editError.classList.remove("hidden");
          }
        },
        selectedFile
      );
      section.appendChild(el("button", { class: "secondary", text: t("pack.review.backToTree"), onclick: showTreeOverview }));
      section.appendChild(relEditor.el);
    }

    showTreeOverview();

    app.innerHTML = "";
    app.appendChild(el("div", { class: "card" }, [
      el("h1", { text: t("pack.review.fileRelations") }),
      el("p", { class: "muted", text: t("relationships.help") }),
      section, editError,
    ]));
  } catch (e) { showError(e); }
}
