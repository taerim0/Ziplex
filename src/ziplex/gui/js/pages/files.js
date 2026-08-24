// The sidebar's Files section (the sortable/filterable table, plus a
// single file's detail page) -- see app.js's header comment for the
// overall module split.

import { app, nav, el, api, getAif, getProject, setStale, showError, showLoading, confidenceLevel, copyButton, startStaleWatch } from "../app.js";
import { t } from "../i18n.js";

export async function renderFiles() {
  nav.classList.remove("hidden");
  showLoading();
  try {
    const data = await api("/api/files", { aif_path: getAif(), project_path: getProject() });
    setStale(data._stale);
    startStaleWatch(getProject(), getAif());
    delete data._stale;

    const filterInput = el("input", { type: "text", placeholder: t("files.searchPlaceholder") });
    const tbody = el("tbody");

    // null = original (server) order; otherwise toggled asc/desc on header
    // click -- e.g. sorting by confidence ascending to triage the worst
    // summaries first is a real workflow this table couldn't support before.
    let sortKey = null;
    let sortDir = 1;
    function sortArrow(key) { return sortKey === key ? (sortDir === 1 ? " ▲" : " ▼") : ""; }
    const nameTh = el("th", { text: t("files.nameHeader", { arrow: sortArrow("name") }), class: "sortable" });
    const confTh = el("th", { text: t("files.confidenceHeader", { arrow: sortArrow("confidence") }), class: "sortable" });
    for (const [th, key] of [[nameTh, "name"], [confTh, "confidence"]]) {
      th.addEventListener("click", () => {
        sortDir = sortKey === key ? -sortDir : 1;
        sortKey = key;
        nameTh.textContent = t("files.nameHeader", { arrow: sortArrow("name") });
        confTh.textContent = t("files.confidenceHeader", { arrow: sortArrow("confidence") });
        draw();
      });
    }
    const table = el("table", {}, [
      el("thead", {}, el("tr", {}, [nameTh, el("th", { text: t("files.summaryHeader") }), confTh])),
      tbody,
    ]);

    function draw() {
      const q = filterInput.value.toLowerCase();
      let entries = Object.entries(data).filter(([name, info]) =>
        !q || name.toLowerCase().includes(q) || (info.summary || "").toLowerCase().includes(q));
      if (sortKey) {
        entries = entries.slice().sort(([an, ai], [bn, bi]) => {
          const [av, bv] = sortKey === "name" ? [an, bn] : [ai.confidence ?? 1.0, bi.confidence ?? 1.0];
          return av < bv ? -sortDir : av > bv ? sortDir : 0;
        });
      }
      tbody.innerHTML = "";
      for (const [name, info] of entries) {
        const conf = info.confidence ?? 1.0;
        const level = confidenceLevel(conf);
        const row = el("tr", { class: `file-row${level === "low" ? " low-confidence" : ""}`, onclick: () => { location.hash = `#/files/${encodeURIComponent(name)}`; } }, [
          el("td", { text: name }),
          el("td", { text: info.summary || "" }),
          el("td", { class: `confidence ${level}`, text: conf.toFixed(2) }),
        ]);
        tbody.appendChild(row);
      }
    }
    filterInput.addEventListener("input", draw);
    draw();

    app.innerHTML = "";
    app.appendChild(el("div", { class: "toolbar" }, filterInput));
    app.appendChild(table);
  } catch (e) { showError(e); }
}

export async function renderFileDetail(name, params) {
  nav.classList.remove("hidden");
  showLoading();
  try {
    const [files, dependents, blastRadius, detail] = await Promise.all([
      api("/api/files", { aif_path: getAif() }),
      api("/api/dependents", { aif_path: getAif(), file: name }),
      api("/api/blast_radius", { aif_path: getAif(), file: name }),
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

    app.innerHTML = "";
    app.appendChild(el("div", { class: "card" }, [
      el("h1", { text: name }),
      el("p", { text: info.summary || "" }),
      el("h3", { text: t("fileDetail.dependents") }), fileList(dependents),
      el("h3", { text: t("fileDetail.blastRadius") }), fileList(blastRadius),
      el("h3", { text: "Detail" }),
      el("pre", { text: detail.compressed || t("fileDetail.noContent") }),
      el("div", { class: "copy-row" }, copyButton(fullText, t("fileDetail.copyAll"))),
    ]));
  } catch (e) { showError(e); }
}
