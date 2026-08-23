// The sidebar's Search section -- see app.js's header comment for the
// overall module split.

import { app, nav, el, api, getProject } from "../app.js";
import { t } from "../i18n.js";

export function renderSearch() {
  nav.classList.remove("hidden");
  app.innerHTML = "";

  const patternInput = el("input", { type: "text", placeholder: t("search.patternPlaceholder") });
  const ctxInput = el("input", { type: "text", value: "0", style: "width:60px" });
  const ignoreCaseInput = el("input", { type: "checkbox" });
  const results = el("div");

  async function run() {
    const pattern = patternInput.value.trim();
    if (!pattern) return;
    results.innerHTML = t("search.searching");
    try {
      const matches = await api("/api/search", {
        project_path: getProject(),
        pattern,
        context_lines: ctxInput.value || 0,
        ignore_case: ignoreCaseInput.checked,
      });
      results.innerHTML = "";
      if (!matches.length) { results.appendChild(el("p", { class: "muted", text: t("search.noResults") })); return; }
      for (const m of matches) {
        const lines = [
          ...m.context_before.map(l => el("div", { class: "ctx-line", text: l })),
          el("div", { class: "match-line", text: m.text }),
          ...m.context_after.map(l => el("div", { class: "ctx-line", text: l })),
        ];
        const loc = el("div", { class: "loc", text: `${m.file}:${m.line}`, onclick: () => {
          const start = Math.max(1, m.line - 5), end = m.line + 5;
          location.hash = `#/files/${encodeURIComponent(m.file)}?start=${start}&end=${end}`;
        } });
        results.appendChild(el("div", { class: "search-result" }, [loc, el("pre", {}, lines)]));
      }
    } catch (e) {
      results.innerHTML = "";
      results.appendChild(el("div", { class: "error", text: e.message }));
    }
  }
  patternInput.addEventListener("keydown", e => { if (e.key === "Enter") run(); });

  app.appendChild(el("div", { class: "toolbar" }, [
    patternInput,
    el("label", { text: "context", style: "margin:0" }), ctxInput,
    el("label", { text: "ignore case", style: "margin:0;display:flex;align-items:center;gap:4px" }, ignoreCaseInput),
    el("button", { text: t("search.button"), onclick: run }),
  ]));
  app.appendChild(results);
}
