// Read-only dependency-graph views for pages/relationships.js:
// renderMiniGraph() (one file's ego-graph), renderDependencyTreeOverview()
// (whole-project collapsible tree) and renderRelationshipViewer()
// (master-detail: searchable file list + the selected file's edges).

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
// source of truth. Clicking an internal neighbor node jumps the viewer's
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

// Whole-project tree over a {file: {internal, external}} map, roots first
// (a root is a file nothing else imports). Native <details> handles
// expand/collapse; clicking a file row calls onSelectFile(name). A file
// already rendered elsewhere shows as a shallow "already shown" row, and a
// true cycle on the current path is flagged (🔁) with a summary banner.
export function renderDependencyTreeOverview(tree, allFiles, onSelectFile) {
  let cycleCount = 0; // true cycles only, for the banner

  function fileRow(name, note, isCycle) {
    const row = el("div", { class: `tree-row${isCycle ? " tree-cycle" : ""}` }, [
      el("span", { text: "📄 " }),
      isCycle ? el("span", { class: "tree-flag", text: "🔁 " }) : null,
      el("span", { class: "tree-name", text: name }),
      note ? el("span", { class: `${isCycle ? "tree-cycle-note" : "muted"}`, text: ` ${note}` }) : null,
    ]);
    row.addEventListener("click", (e) => { e.preventDefault(); onSelectFile(name); });
    return row;
  }

  // `ancestors`: files on the current root-to-here path (cycle guard).
  // `seen`: shared across the whole render; a file expands only the first
  // time it appears, which bounds rendered nodes to the graph's size (a
  // heavily shared file used to re-expand per dependent and hang the page).
  function buildNode(name, ancestors, seen) {
    const deps = tree[name] || { internal: [], external: [] };
    const children = [];
    for (const dep of deps.internal) {
      if (ancestors.has(dep)) {
        cycleCount++;
        children.push(fileRow(dep, t("graph.tree.cycleSkipped"), true));
        continue;
      }
      if (seen.has(dep)) {
        children.push(fileRow(dep, t("graph.tree.alreadyShown")));
        continue;
      }
      seen.add(dep);
      children.push(buildNode(dep, new Set([...ancestors, dep]), seen));
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
  // Fully cyclic graph with no natural root: fall back to every file.
  if (!roots.length && allFiles.length) roots = allFiles;

  // Checked at the root level too: in the all-roots fallback, a root may
  // already have been expanded under an earlier root.
  const seen = new Set();
  for (const name of roots) {
    if (seen.has(name)) {
      box.appendChild(fileRow(name, t("graph.tree.alreadyShown")));
      continue;
    }
    seen.add(name);
    box.appendChild(buildNode(name, new Set([name]), seen));
  }
  if (!roots.length) box.appendChild(el("p", { class: "muted", text: t("graph.tree.empty") }));
  if (cycleCount > 0) {
    box.prepend(el("div", { class: "tree-cycles-banner", text: t("graph.tree.cyclesSummary", { n: cycleCount }) }));
  }
  return box;
}

// Master-detail viewer: a searchable file list on the left and, on the
// right, only the selected file's ego-graph and edge lists, so the amount
// rendered doesn't grow with project size.
export function renderRelationshipViewer(tree, allFiles, initialSelected) {
  const box = el("div", { class: "rel-master-detail" });
  let selected = initialSelected && allFiles.includes(initialSelected) ? initialSelected : (allFiles[0] || null);

  const searchInput = el("input", { type: "text", placeholder: t("graph.editor.searchPlaceholder") });
  const listPane = el("div", { class: "rel-file-list" });
  const detailPane = el("div", { class: "rel-detail-pane" });

  // The tree only records outgoing edges; precompute "who imports me" once.
  const reverseDeps = new Map();
  for (const f of allFiles) {
    for (const dep of tree[f]?.internal || []) {
      if (!reverseDeps.has(dep)) reverseDeps.set(dep, []);
      reverseDeps.get(dep).push(f);
    }
  }
  const dependentsOf = (name) => reverseDeps.get(name) || [];

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
      const deps = tree[name] || { internal: [], external: [] };
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
    const deps = tree[name] || { internal: [], external: [] };
    const parents = dependentsOf(name);
    const children = [
      ...deps.internal.map(c => ({ name: c, external: false })),
      ...deps.external.map(c => ({ name: c, external: true })),
    ];

    detailPane.appendChild(el("div", { class: "file-edit-name" }, [
      el("a", { href: `#/files/${encodeURIComponent(name)}`, text: `📄 ${name}` }),
    ]));
    detailPane.appendChild(renderMiniGraph(name, parents, children, selectFile));

    const edgeList = el("div", { class: "rel-edges" });
    for (const child of deps.internal) {
      edgeList.appendChild(el("div", { class: "rel-edge rel-edge-link", onclick: () => selectFile(child) },
        el("span", { text: `→ 📄 ${child}` })));
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
  }

  searchInput.addEventListener("input", drawList);
  drawList();
  drawDetail();

  box.appendChild(el("div", { class: "rel-list-pane" }, [searchInput, listPane]));
  box.appendChild(detailPane);
  return box;
}