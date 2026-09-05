// The sidebar's Files section (a collapsible folder tree, plus a single
// file's detail page) -- see app.js's header comment for the overall
// module split. Files used to be a flat sortable table; redesigned into a
// folder tree (real folder role summaries alongside each folder, not just
// a flat file list) after a reported usability gap: a human browsing the
// GUI to understand a project had no sense of directory structure at all.

import { app, nav, el, api, getAif, getProject, setStale, showError, showLoading, confidenceLevel, copyButton, startStaleWatch } from "../app.js";
import { t } from "../i18n.js";

// Groups a flat {name: {...}} map into a nested {folders: {name: node},
// files: [name, ...]} tree by path segment -- mirrors folder_summary.py's
// own group_files_by_folder() one level at a time (each file lands under
// its *immediate* parent only; a deeper file's own ancestors are built up
// by the recursion in folderNode() below, not duplicated into every
// ancestor's own `files` list) so the two trees agree on which folder
// summary belongs to which files.
function buildFolderTree(files) {
  const root = { folders: {}, files: [] };
  for (const name of Object.keys(files)) {
    const parts = name.split("/");
    const filename = parts.pop();
    let node = root;
    for (const part of parts) {
      node = node.folders[part] || (node.folders[part] = { folders: {}, files: [] });
    }
    node.files.push(name);
  }
  return root;
}

export async function renderFiles() {
  nav.classList.remove("hidden");
  showLoading();
  try {
    const [files, folders] = await Promise.all([
      api("/api/files", { aif_path: getAif(), project_path: getProject() }),
      api("/api/folders", { aif_path: getAif() }),
    ]);
    setStale(files._stale);
    startStaleWatch(getProject(), getAif());
    delete files._stale;

    const filterInput = el("input", { type: "text", placeholder: t("files.searchPlaceholder") });
    const treeBox = el("div", { class: "tree-overview" });

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

      const summary = folders[path]?.summary;
      // Just the folder's own last segment, not the full path -- it's
      // already visually nested under its parent, so repeating the full
      // path here (backend/services/utils, and so on going deeper) is
      // both redundant and, for a deeply nested project, the single
      // biggest source of cramped/wrapped tree rows.
      const displayName = path === "." ? t("files.rootFolder") : path.split("/").pop();
      const label = el("summary", {}, [
        el("span", { text: "📁 " }),
        el("span", { class: "tree-name tree-name-fixed", text: displayName }),
        summary ? el("span", { class: "muted tree-desc", text: ` — ${summary}` }) : null,
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
      const rootNode = folderNode(".", buildFolderTree(files), matches);
      treeBox.appendChild(rootNode || el("p", { class: "muted", text: t("files.noResults") }));
    }
    filterInput.addEventListener("input", draw);
    draw();

    app.innerHTML = "";
    app.appendChild(el("div", { class: "toolbar" }, filterInput));
    app.appendChild(treeBox);
  } catch (e) { showError(e); }
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
  try {
    let includeTextRefs = true;
    const [files, { dependents, blastRadius }, detail] = await Promise.all([
      api("/api/files", { aif_path: getAif() }),
      fetchRelationships(name, includeTextRefs),
      api("/api/detail", { aif_path: getAif(), file: name, start_line: params.get("start"), end_line: params.get("end") }),
    ]);
    const info = files[name] || {};

    function fileList(names) {
      if (!names.length) return el("p", { class: "muted", text: t("fileDetail.none") });
      return el("ul", { class: "file-list" }, names.map(n =>
        el("li", {}, el("a", { href: `#/files/${encodeURIComponent(n)}`, text: n }))
      ));
    }

    const fullText = () => `# ${name}\n\n${info.summary || ""}\n\n\`\`\`\n${detail.compressed}\n\`\`\``;

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
        showLoading();
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
      el("p", { text: info.summary || "" }),
      textRefToggle,
      relSection,
      el("h3", { text: "Detail" }),
      el("pre", { text: detail.compressed || t("fileDetail.noContent") }),
      el("div", { class: "copy-row" }, copyButton(fullText, t("fileDetail.copyAll"))),
    ]));
  } catch (e) { showError(e); }
}
