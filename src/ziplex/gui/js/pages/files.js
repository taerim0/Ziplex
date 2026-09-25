// The sidebar's Files section (a collapsible folder tree, plus a single
// file's detail page) -- see app.js's header comment for the overall
// module split. Files used to be a flat sortable table; redesigned into a
// folder tree (real folder role summaries alongside each folder, not just
// a flat file list) after a reported usability gap: a human browsing the
// GUI to understand a project had no sense of directory structure at all.

import { app, nav, el, api, apiPost, getAif, getProject, setStale, showError, showLoading, confidenceLevel, copyButton, startStaleWatch, buildPathTree, createSummaryEditor, navigationToken, isCurrentNavigation } from "../app.js";
import { t } from "../i18n.js";

export async function renderFiles() {
  nav.classList.remove("hidden");
  showLoading();
  const navToken = navigationToken();
  try {
    const [files, folders] = await Promise.all([
      api("/api/files", { aif_path: getAif(), project_path: getProject() }),
      api("/api/folders", { aif_path: getAif() }),
    ]);
    if (!isCurrentNavigation(navToken)) return;
    setStale(files._stale);
    startStaleWatch(getProject(), getAif());
    delete files._stale;

    const filterInput = el("input", { type: "text", placeholder: t("files.searchPlaceholder") });
    const treeBox = el("div", { class: "tree-overview" });

    // Independent of each folder's own <details>/<summary> expand-collapse
    // (that toggles whether a folder's *children* show at all) -- this one
    // toggles whether the summary *text* next to an already-visible file/
    // folder row shows, everywhere in the tree at once. Both a file row's
    // and a folder label's summary span already share the one `.tree-desc`
    // class, so a single CSS rule on `treeBox` covers every row without
    // walking the tree -- no per-row toggle state to track, and it survives
    // re-draw() (a filter keystroke) since the class lives on the
    // container, not on rows draw() throws away and rebuilds every time.
    let summariesHidden = false;
    const summaryToggleBtn = el("button", { class: "secondary", text: t("files.hideSummaries") });
    summaryToggleBtn.addEventListener("click", () => {
      summariesHidden = !summariesHidden;
      treeBox.classList.toggle("hide-summaries", summariesHidden);
      summaryToggleBtn.textContent = t(summariesHidden ? "files.showSummaries" : "files.hideSummaries");
    });

    function fileRow(name) {
      const info = files[name] || {};
      const conf = info.confidence ?? 1.0;
      const level = confidenceLevel(conf);
      const row = el("div", { class: `tree-row${level === "low" ? " tree-flagged" : ""}` }, [
        el("span", { text: "📄 " }),
        el("span", { class: "tree-name tree-name-fixed", text: name.split("/").pop() }),
        el("span", { class: `confidence ${level}`, text: conf.toFixed(2) }),
        info.summary ? el("span", { class: "muted tree-desc", text: ` — ${info.summary}` }) : null,
      ]);
      row.addEventListener("click", () => { location.hash = `#/files/${encodeURIComponent(name)}`; });
      return row;
    }

    // path is the folder's own key into `folders` ("." for the project
    // root, matching folder_summary.group_files_by_folder()'s own
    // convention) -- not necessarily unique display text, so the root's
    // own label is swapped for a translated placeholder rather than a
    // bare ".".
    function folderNode(path, node, matches) {
      const fileRows = node.files.filter(name => matches.has(name)).map(fileRow);
      const childNodes = Object.entries(node.folders)
        .map(([childName, childNode]) => {
          const childPath = path === "." ? childName : `${path}/${childName}`;
          return folderNode(childPath, childNode, matches);
        })
        .filter(Boolean);

      if (!fileRows.length && !childNodes.length) return null;

      // Just the folder's own last segment, not the full path -- it's
      // already visually nested under its parent, so repeating the full
      // path here (backend/services/utils, and so on going deeper) is
      // both redundant and, for a deeply nested project, the single
      // biggest source of cramped/wrapped tree rows.
      const displayName = path === "." ? t("files.rootFolder") : path.split("/").pop();

      // Editable folder summary -- the same post-pack fix-without-a-re-pack
      // escape hatch renderFileDetail's per-file summary editor already
      // has (see that function's own comment). The tricky part reported
      // directly: a folder row is a native <details>/<summary> disclosure
      // triangle, so clicking *anywhere* on the row already means
      // "toggle open/closed" -- a naive "click the summary text to edit"
      // affordance would be indistinguishable from that and unreachable.
      // Solved with a small dedicated ✏️ button instead (same icon/pattern
      // as the per-file editor, for consistency): its own click handler
      // calls preventDefault()+stopPropagation() first, which is what
      // actually suppresses <summary>'s native "toggle on click" behavior
      // (the toggle is that click event's default action) -- every other
      // click on the row (the folder name, the description text) still
      // toggles exactly as before.
      const editor = createSummaryEditor({
        getValue: () => folders[path]?.summary || "",
        formatDisplay: (v) => v ? ` — ${v}` : "",
        displayClass: "muted tree-desc",
        editBtnClass: "secondary tree-edit-btn",
        rows: "2",
        stopPropagation: true,
        buildEditRow: (textarea, saveBtn, cancelBtn) => el("span", { class: "tree-folder-edit" }, [textarea, saveBtn, cancelBtn]),
        onSave: async (newSummary) => {
          await apiPost("/api/folders/summary", { aif_path: getAif(), folder: path, summary: newSummary });
          folders[path] = { ...(folders[path] || {}), summary: newSummary };
        },
      });

      const label = el("summary", {}, [
        el("span", { text: "📁 " }),
        el("span", { class: "tree-name tree-name-fixed", text: displayName }),
        editor.displayEl,
        // Only a folder with a saved summary can be edited: a pure container
        // (no files of its own) or a one-file folder has no aif.folders entry,
        // so /api/folders/summary would 404 on save.
        ...(folders[path] ? [editor.editBtn] : []),
        editor.editRow,
        editor.errorEl,
      ]);
      return el("details", { class: "tree-node", open: "" }, [
        label,
        el("div", { class: "tree-children" }, [...childNodes, ...fileRows]),
      ]);
    }

    function draw() {
      const q = filterInput.value.toLowerCase();
      const matches = new Set(
        Object.entries(files)
          .filter(([name, info]) => !q || name.toLowerCase().includes(q) || (info.summary || "").toLowerCase().includes(q))
          .map(([name]) => name)
      );

      treeBox.innerHTML = "";
      // buildPathTree() groups by *immediate* parent one level at a time,
      // mirroring folder_summary.py's own group_files_by_folder() -- so the
      // two trees agree on which folder summary belongs to which files.
      const rootNode = folderNode(".", buildPathTree(Object.keys(files)), matches);
      treeBox.appendChild(rootNode || el("p", { class: "muted", text: t("files.noResults") }));
    }
    filterInput.addEventListener("input", draw);
    draw();

    app.innerHTML = "";
    app.appendChild(el("div", { class: "toolbar" }, [filterInput, summaryToggleBtn]));
    app.appendChild(treeBox);
  } catch (e) { if (isCurrentNavigation(navToken)) showError(e); }
}

async function fetchRelationships(name, includeTextRefs) {
  const [dependents, blastRadius] = await Promise.all([
    api("/api/dependents", { aif_path: getAif(), file: name, include_text_refs: includeTextRefs }),
    api("/api/blast_radius", { aif_path: getAif(), file: name, include_text_refs: includeTextRefs }),
  ]);
  return { dependents, blastRadius };
}

export async function renderFileDetail(name, params) {
  nav.classList.remove("hidden");
  showLoading();
  const navToken = navigationToken();
  try {
    let includeTextRefs = true;
    const [files, { dependents, blastRadius }, detail] = await Promise.all([
      api("/api/files", { aif_path: getAif() }),
      fetchRelationships(name, includeTextRefs),
      api("/api/detail", { aif_path: getAif(), file: name, start_line: params.get("start"), end_line: params.get("end") }),
    ]);
    if (!isCurrentNavigation(navToken)) return;
    const info = files[name] || {};

    function fileList(names) {
      if (!names.length) return el("p", { class: "muted", text: t("fileDetail.none") });
      return el("ul", { class: "file-list" }, names.map(n =>
        el("li", {}, el("a", { href: `#/files/${encodeURIComponent(n)}`, text: n }))
      ));
    }

    const fullText = () => `# ${name}\n\n${info.summary || ""}\n\n\`\`\`\n${detail.compressed}\n\`\`\``;

    // Post-pack summary editing (a real gap found by code review:
    // relationships already had this post-save escape hatch via the
    // Relationships page/`ziplex link`, summaries didn't, even though a
    // one-line text fix is a smaller edit than a graph edge) -- swaps the
    // read-only <p> for a textarea + Save/Cancel in place, no page
    // navigation. `info.summary` is mutated in place on a successful save
    // so fullText()'s Copy button reflects the edit without a re-fetch.
    const editor = createSummaryEditor({
      getValue: () => info.summary || "",
      displayTag: "p",
      buildEditRow: (textarea, saveBtn, cancelBtn) => el("div", {}, [textarea, el("div", { class: "toolbar" }, [saveBtn, cancelBtn])]),
      onSave: async (newSummary) => {
        await apiPost("/api/files/summary", { aif_path: getAif(), file: name, summary: newSummary });
        info.summary = newSummary;
      },
    });
    const summarySection = el("div", {}, [editor.displayEl, editor.editBtn, editor.editRow, editor.errorEl]);

    // A dependent/blast-radius entry reached only via text_references.py's
    // filename-mention matching (a README naming this file, a Godot scene's
    // ext_resource path) rather than a real import -- see
    // file/relationship.py's build_tree() docstring. This toggle is the
    // "certain relationships only" view query_service.py already supports
    // (and the MCP server already exposes) -- until this checkbox, the GUI
    // had no way to request it at all.
    const relSection = el("div", {});
    function drawRelationships(deps, blast) {
      relSection.innerHTML = "";
      relSection.appendChild(el("h3", { text: t("fileDetail.dependents") }));
      relSection.appendChild(fileList(deps));
      relSection.appendChild(el("h3", { text: t("fileDetail.blastRadius") }));
      relSection.appendChild(fileList(blast));
    }
    drawRelationships(dependents, blastRadius);

    const textRefCheckbox = el("input", {
      type: "checkbox",
      checked: "checked",
      onchange: async (e) => {
        includeTextRefs = e.target.checked;
        // Scoped to relSection itself, not showLoading() -- that wipes
        // the whole #app container, which detaches relSection from the
        // live DOM (it's a child of #app) before drawRelationships() ever
        // gets a chance to mutate it, permanently freezing the page on
        // "Loading...".
        relSection.innerHTML = "";
        relSection.appendChild(el("p", { class: "muted loading", text: t("core.loading") }));
        try {
          const fresh = await fetchRelationships(name, includeTextRefs);
          drawRelationships(fresh.dependents, fresh.blastRadius);
        } catch (err) { showError(err); }
      },
    });
    const textRefToggle = el("label", { class: "muted" }, [textRefCheckbox, document.createTextNode(" " + t("fileDetail.includeTextRefs"))]);

    app.innerHTML = "";
    app.appendChild(el("div", { class: "card" }, [
      el("h1", { text: name }),
      summarySection,
      textRefToggle,
      relSection,
      el("h3", { text: "Detail" }),
      el("pre", { text: detail.compressed || t("fileDetail.noContent") }),
      el("div", { class: "copy-row" }, copyButton(fullText, t("fileDetail.copyAll"))),
    ]));
  } catch (e) { if (isCurrentNavigation(navToken)) showError(e); }
}
