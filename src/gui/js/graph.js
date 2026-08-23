// Dependency-graph visualization + editing -- see app.js's header comment
// for the overall module split. This file: svgEl()/truncateTail()/
// shortLabels() (small SVG/label helpers, internal to this module), and
// the three exported components -- renderMiniGraph() (one file's ego-
// graph), renderDependencyTreeOverview() (read-only, collapsible, shown
// first), and renderRelationshipEditor() (master-detail, reached by
// clicking a file in the overview). Used by both the pack review flow
// (pack.js) and the post-pack Relationships page (pages/relationships.js).

import { el } from "./app.js";
import { t } from "./i18n.js";

function svgEl(tag, attrs = {}, children = []) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") n.textContent = v;
    else n.setAttribute(k, v);
  }
  for (const c of [].concat(children)) if (c) n.appendChild(c);
  return n;
}

// Keeps the tail of a string rather than the front when it has to be cut --
// last-resort truncation once shortLabels() below has already reduced a
// relative path down to (usually) just its basename, for the rare case
// where even that basename alone is too long for a node box.
function truncateTail(s, max) {
  return s.length <= max ? s : "…" + s.slice(-(max - 1));
}

// Reduces each of `names` (relative paths) to its basename for display --
// "src/very/deep/path/Component.tsx" and "Component.tsx" both read as just
// "Component.tsx" in a mini graph's fixed-width node boxes, since the
// directory prefix was eating into the truncation budget and clipping the
// actual filename (the identifying part) rather than the path (the noisy
// part). Falls back to "parentDir/basename" only for names whose basename
// collides with another name in this same `names` list -- disambiguation is
// scoped to what's actually shown together in one graph, not the whole
// project, so it only kicks in when it would otherwise be genuinely
// ambiguous on screen. The full path is still always available via the
// node's <title> hover tooltip regardless of which label wins here.
function shortLabels(names) {
  const counts = {};
  for (const n of names) {
    const base = n.split("/").pop();
    counts[base] = (counts[base] || 0) + 1;
  }
  const labels = {};
  for (const n of names) {
    const parts = n.split("/");
    const base = parts.pop();
    labels[n] = counts[base] > 1 && parts.length ? `${parts[parts.length - 1]}/${base}` : base;
  }
  return labels;
}

// Small "ego graph" for one file: direct dependents on the left (arrows
// pointing in) and direct dependencies on the right (arrows pointing out),
// capped at REL_GRAPH_MAX_NEIGHBORS per side so a heavily-shared file (a
// utils module with 50 dependents, say) can't blow up the SVG -- the text
// lists below the graph already enumerate everything; this is a visual aid
// for spotting the shape of a file's relationships at a glance, not the
// source of truth. Clicking an internal neighbor node jumps the editor's
// selection to it (via onSelect), so a human can walk the graph instead of
// re-searching the file list for every hop.
const REL_GRAPH_MAX_NEIGHBORS = 6;

export function renderMiniGraph(name, parents, children, onSelect) {
  const shownParents = parents.slice(0, REL_GRAPH_MAX_NEIGHBORS);
  const extraParents = parents.length - shownParents.length;
  const shownChildren = children.slice(0, REL_GRAPH_MAX_NEIGHBORS);
  const extraChildren = children.length - shownChildren.length;

  const rows = Math.max(shownParents.length, shownChildren.length, 1);
  const rowH = 26;
  const height = rows * rowH + (extraParents || extraChildren ? 18 : 0) + 20;
  const width = 480;
  const midY = height / 2 - (extraParents || extraChildren ? 9 : 0);
  const midX = width / 2;
  const sideMargin = 8;

  const labels = shortLabels([name, ...shownParents, ...shownChildren.map(c => c.name)]);

  const centerLabel = truncateTail(labels[name], 26);
  const centerW = Math.max(90, centerLabel.length * 6.4 + 24);

  // Two markers, not one: an SVG marker's fill doesn't inherit from the
  // line referencing it, so the accent-colored in/out edges and the muted
  // dashed external edges each need their own arrowhead colored to match.
  function arrowMarker(id, fill) {
    return svgEl("marker", {
      id, viewBox: "0 0 10 10", refX: "9", refY: "5",
      markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse",
    }, [svgEl("path", { d: "M0,0 L10,5 L0,10 z", style: `fill:${fill}` })]);
  }

  const svg = svgEl("svg", { class: "rel-graph", viewBox: `0 0 ${width} ${height}`, width: "100%", height: String(height) }, [
    svgEl("defs", {}, [
      arrowMarker("rel-arrow", "var(--accent)"),
      arrowMarker("rel-arrow-muted", "var(--muted)"),
    ]),
  ]);

  function sideNode(itemName, external, x, y, align) {
    const label = truncateTail(labels[itemName], 20);
    const w = Math.max(70, label.length * 6.1 + 16);
    const rectX = align === "left" ? x : x - w;
    const group = svgEl("g", { class: `rel-node${external ? " external" : ""}` }, [
      svgEl("rect", { x: rectX, y: y - 11, width: w, height: 22, rx: 5 }),
      svgEl("text", { x: rectX + w / 2, y: y + 4, "text-anchor": "middle", text: label }),
      svgEl("title", { text: itemName }),
    ]);
    if (!external && onSelect) {
      group.style.cursor = "pointer";
      group.addEventListener("click", () => onSelect(itemName));
    }
    return { group, edgeX: align === "left" ? rectX + w : rectX };
  }

  function rowY(i) { return midY - ((rows - 1) * rowH) / 2 + i * rowH; }

  shownParents.forEach((p, i) => {
    const y = rowY(i);
    const { group, edgeX } = sideNode(p, false, sideMargin, y, "left");
    svg.appendChild(svgEl("path", {
      class: "rel-edge-line in", "marker-end": "url(#rel-arrow)",
      d: `M${edgeX},${y} C${(edgeX + midX - centerW / 2) / 2},${y} ${(edgeX + midX - centerW / 2) / 2},${midY} ${midX - centerW / 2 - 4},${midY}`,
    }));
    svg.appendChild(group);
  });
  if (extraParents > 0) {
    svg.appendChild(svgEl("text", { class: "rel-graph-more", x: sideMargin, y: rowY(shownParents.length - 1) + rowH, text: t("graph.more", { n: extraParents }) }));
  }

  shownChildren.forEach((c, i) => {
    const y = rowY(i);
    const { group, edgeX } = sideNode(c.name, c.external, width - sideMargin, y, "right");
    svg.appendChild(svgEl("path", {
      class: `rel-edge-line out${c.external ? " external" : ""}`, "marker-end": `url(#${c.external ? "rel-arrow-muted" : "rel-arrow"})`,
      d: `M${midX + centerW / 2 + 4},${midY} C${(edgeX + midX + centerW / 2) / 2},${midY} ${(edgeX + midX + centerW / 2) / 2},${y} ${edgeX - 4},${y}`,
    }));
    svg.appendChild(group);
  });
  if (extraChildren > 0) {
    svg.appendChild(svgEl("text", { class: "rel-graph-more", x: width - sideMargin, y: rowY(shownChildren.length - 1) + rowH, "text-anchor": "end", text: t("graph.more", { n: extraChildren }) }));
  }

  svg.appendChild(svgEl("g", { class: "rel-node rel-node-center" }, [
    svgEl("rect", { x: midX - centerW / 2, y: midY - 13, width: centerW, height: 26, rx: 6 }),
    svgEl("text", { x: midX, y: midY + 4, "text-anchor": "middle", text: centerLabel }),
    svgEl("title", { text: name }),
  ]));

  return svg;
}

// Read-only "whole project at a glance" view over the same build_tree()-
// shaped dependency tree ({file: {internal: [...], external: [...]}})
// renderRelationshipEditor below edits -- shown first when a pack review's
// relationship section loads (see pack.js's showReviewState()), instead of
// jumping straight into per-file edit mode for the first flagged file the
// way an earlier version did. The idea: let a human spot what actually
// looks wrong by eye across the *whole* tree first, and only drop into the
// edit UI for a file once they've decided (by looking, not by clicking
// through a search list one file at a time) that it needs a change.
//
// Same roots-first + cycle-guarded traversal as corrector.py's terminal
// print_current_tree() (a "root" is any file nothing else's `internal` list
// points at) -- kept in sync with that function deliberately, since the two
// are the browser and terminal versions of the identical judgment call.
// Rendered with native <details>/<summary> so expand/collapse needs no JS
// of its own and a large project (dozens of files) stays scannable by
// collapsing subtrees rather than becoming one long scroll.
//
// Clicking a file's own row (icon + name) calls onSelectFile(name) instead
// of toggling the <details> it may sit inside -- event.preventDefault() in
// that row's click handler suppresses the native disclosure-triangle
// toggle for clicks on the row itself, while a click on the actual triangle
// (part of <summary>, not this row) still expands/collapses normally, the
// same split VS Code's own file tree uses (chevron toggles, label opens).
export function renderDependencyTreeOverview(tree, allFiles, flaggedFiles, onSelectFile) {
  const flagged = new Set(flaggedFiles);

  function fileRow(name, note) {
    const row = el("div", { class: `tree-row${flagged.has(name) ? " tree-flagged" : ""}` }, [
      el("span", { text: "📄 " }),
      flagged.has(name) ? el("span", { class: "tree-flag", text: "⚠️ " }) : null,
      el("span", { class: "tree-name", text: name }),
      note ? el("span", { class: "muted", text: ` ${note}` }) : null,
    ]);
    row.addEventListener("click", (e) => { e.preventDefault(); onSelectFile(name); });
    return row;
  }

  function buildNode(name, ancestors) {
    const deps = tree[name] || { internal: [], external: [] };
    const children = [];
    for (const dep of deps.internal) {
      if (ancestors.has(dep)) {
        children.push(fileRow(dep, t("graph.tree.cycleSkipped")));
        continue;
      }
      children.push(buildNode(dep, new Set([...ancestors, dep])));
    }
    for (const ext of deps.external) {
      children.push(el("div", { class: "tree-row muted" }, [el("span", { text: "📦 " }), el("span", { text: ext })]));
    }

    if (!children.length) return fileRow(name);
    return el("details", { class: "tree-node", open: "" }, [
      el("summary", {}, [fileRow(name)]),
      el("div", { class: "tree-children" }, children),
    ]);
  }

  const isChild = new Set();
  for (const name of allFiles) {
    for (const dep of tree[name]?.internal || []) isChild.add(dep);
  }

  const box = el("div", { class: "tree-overview" });
  let roots = allFiles.filter(name => !isChild.has(name));
  // Every file is somebody's dependency -- a full cycle with no natural
  // root (see file/relationship.py's own docstring re: mutual-import
  // cycles). corrector.py's terminal tree has the identical gap; rather
  // than silently rendering nothing here, fall back to a flat list so
  // "what files exist" still has an obvious answer.
  if (!roots.length && allFiles.length) roots = allFiles;

  for (const name of roots) box.appendChild(buildNode(name, new Set([name])));
  if (!roots.length) box.appendChild(el("p", { class: "muted", text: t("graph.tree.empty") }));
  return box;
}

// Master-detail editor over a build_tree()-shaped dependency tree
// ({file: {internal: [...], external: [...]}}). Rendering every file's full
// edge list at once (an earlier version did this) doesn't scale past a
// couple dozen files: the page turns into one long scroll, and each file's
// "add dependency" dropdown lists every other file in the project with no
// way to search it. This instead shows a searchable file list on the left
// and, on the right, only the selected file's own relationships -- an ego
// graph (renderMiniGraph, above) plus the same edit controls the old
// version had, so the amount rendered no longer grows with project size.
// `dependencies` is a graph, not a tree -- a file can legitimately be
// depended on by more than one other file -- so this still edits one edge
// (file -> target) at a time instead of reparenting a whole nested subtree
// the way an earlier drag-and-drop version did (that collapsed ALL of a
// shared file's references down to wherever it got dropped, which is almost
// never what you want). onLink/onUnlink(file, target) are called on each
// add/remove click; the caller is expected to redraw via the returned
// .setTree() once the server confirms the change (see /api/pack/link,
// /api/pack/unlink in pack.js's showReviewState).
export function renderRelationshipEditor(tree, allFiles, onLink, onUnlink, initialSelected) {
  const box = el("div", { class: "rel-master-detail" });
  let currentTree = tree;
  let selected = initialSelected && allFiles.includes(initialSelected) ? initialSelected : (allFiles[0] || null);

  const searchInput = el("input", { type: "text", placeholder: t("graph.editor.searchPlaceholder") });
  const listPane = el("div", { class: "rel-file-list" });
  const detailPane = el("div", { class: "rel-detail-pane" });

  // Everyone whose own `internal` list points at `name` -- the tree only
  // records outgoing edges per file, so "who depends on me" (shown as the
  // graph's left/parent side) has to be derived by scanning every file
  // rather than looked up directly. Precomputed once per currentTree
  // (buildReverseDeps(), called on setup and again in setTree()) instead of
  // rescanning allFiles from dependentsOf() itself -- drawList() calls it
  // once per visible row, and the search box's "input" listener re-runs
  // drawList() on every keystroke, so an O(n) scan per row made a full
  // relationship-editor redraw O(n²) in the file count.
  let reverseDeps = new Map();
  function buildReverseDeps() {
    reverseDeps = new Map();
    for (const f of allFiles) {
      for (const dep of currentTree[f]?.internal || []) {
        if (!reverseDeps.has(dep)) reverseDeps.set(dep, []);
        reverseDeps.get(dep).push(f);
      }
    }
  }
  function dependentsOf(name) {
    return reverseDeps.get(name) || [];
  }
  buildReverseDeps();

  function selectFile(name) {
    selected = name;
    drawList();
    drawDetail();
  }

  function drawList() {
    const q = searchInput.value.trim().toLowerCase();
    listPane.innerHTML = "";
    let shown = 0;
    for (const name of allFiles) {
      if (q && !name.toLowerCase().includes(q)) continue;
      shown++;
      const deps = currentTree[name] || { internal: [], external: [] };
      const hasEdges = deps.internal.length > 0 || deps.external.length > 0 || dependentsOf(name).length > 0;
      listPane.appendChild(el("div", {
        class: `rel-list-row${name === selected ? " active" : ""}`,
        onclick: () => selectFile(name),
      }, [
        el("span", { class: `rel-dot${hasEdges ? " has-edges" : ""}` }),
        el("span", { class: "rel-list-name", text: name }),
      ]));
    }
    if (!shown) listPane.appendChild(el("p", { class: "muted", style: "padding:10px", text: t("graph.editor.noMatch") }));
  }

  function drawDetail() {
    detailPane.innerHTML = "";
    if (!selected) {
      detailPane.appendChild(el("p", { class: "muted", text: t("graph.editor.selectPrompt") }));
      return;
    }
    const name = selected;
    const deps = currentTree[name] || { internal: [], external: [] };
    const parents = dependentsOf(name);
    const children = [
      ...deps.internal.map(c => ({ name: c, external: false })),
      ...deps.external.map(c => ({ name: c, external: true })),
    ];

    detailPane.appendChild(el("div", { class: "file-edit-name", text: `📄 ${name}` }));
    detailPane.appendChild(renderMiniGraph(name, parents, children, selectFile));

    const edgeList = el("div", { class: "rel-edges" });
    for (const child of deps.internal) {
      const unlinkButton = el("button", { class: "secondary", text: t("graph.editor.unlink") });
      unlinkButton.addEventListener("click", () => onUnlink(name, child));
      edgeList.appendChild(el("div", { class: "rel-edge" }, [
        el("span", { text: `→ 📄 ${child}` }), unlinkButton,
      ]));
    }
    for (const ext of deps.external) {
      edgeList.appendChild(el("div", { class: "rel-edge muted" }, el("span", { text: `→ 📦 ${ext}` })));
    }
    if (!deps.internal.length && !deps.external.length) {
      edgeList.appendChild(el("p", { class: "muted", text: t("graph.editor.noDependencies") }));
    }
    detailPane.appendChild(edgeList);

    if (parents.length) {
      detailPane.appendChild(el("p", { class: "muted", text: t("graph.editor.dependentsOf", { n: parents.length, list: parents.join(", ") }) }));
    }

    const targetListId = "rel-target-options";
    const targetInput = el("input", { type: "text", list: targetListId, placeholder: t("graph.editor.linkSearchPlaceholder") });
    const targetOptions = el("datalist", { id: targetListId },
      allFiles.filter(f => f !== name && !deps.internal.includes(f)).map(f => el("option", { value: f })));
    const linkButton = el("button", { class: "secondary", text: t("graph.editor.addLink"), onclick: () => {
      const target = targetInput.value.trim();
      if (target && target !== name) onLink(name, target);
    } });
    detailPane.appendChild(el("div", { class: "toolbar" }, [targetInput, targetOptions, linkButton]));
  }

  searchInput.addEventListener("input", drawList);
  drawList();
  drawDetail();

  box.appendChild(el("div", { class: "rel-list-pane" }, [searchInput, listPane]));
  box.appendChild(detailPane);

  return {
    el: box,
    setTree: (tree) => { currentTree = tree; buildReverseDeps(); drawList(); drawDetail(); },
  };
}
