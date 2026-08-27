// The sidebar's Overview section -- see app.js's header comment for the
// overall module split.

import { app, nav, el, api, getAif, getProject, setStale, showError, showLoading, copyButton, startStaleWatch } from "../app.js";
import { t } from "../i18n.js";

export async function renderOverview() {
  nav.classList.remove("hidden");
  showLoading();
  try {
    const data = await api("/api/overview", { aif_path: getAif(), project_path: getProject() });
    setStale(data._stale);
    startStaleWatch(getProject(), getAif());
    const rulesList = el("ul", {}, (data.rules || []).map(r => el("li", { text: r })));
    const techStack = data.project.tech_stack || [];
    const techStackList = techStack.length
      ? el("ul", {}, techStack.map(stack => el("li", {}, [
          el("strong", { text: `${stack.language} (${stack.manifest})` }),
          el("span", { text: ` ${t("overview.techStackDeps", { n: stack.dependencies.length })}: ` }),
          el("span", { text: (stack.dependencies || []).join(", ") + (stack.dependencies_truncated ? " …" : "") }),
        ])))
      : null;
    // named `tok`, not `t` -- this file's own t() (i18n.js) would
    // otherwise be shadowed inside this callback's scope.
    const tokenRows = Object.entries(data.tokens || {}).map(([model, tok]) =>
      el("tr", {}, [
        el("td", { text: model }),
        el("td", { text: `${tok.original} → ${tok.compressed}` }),
        el("td", { text: `${tok.saved_pct}%` }),
      ])
    );

    const summaryText = () =>
      `# ${data.project.name}\n\n${data.project.prompt || ""}\n\n` +
      (techStack.length
        ? `## Tech Stack\n${techStack.map(s => `- ${s.language} (${s.manifest}): ${(s.dependencies || []).join(", ")}`).join("\n")}\n\n`
        : "") +
      `## Rules\n` + (data.rules || []).map(r => `- ${r}`).join("\n");

    app.innerHTML = "";
    app.appendChild(el("div", { class: "card" }, [
      el("h1", { text: data.project.name || t("overview.untitled") }),
      el("h2", { text: t("overview.fileCount", { n: data.file_count }) }),
      el("p", { text: data.project.prompt || "" }),
      techStackList ? el("h3", { text: t("overview.techStackHeading") }) : null,
      techStackList,
      el("h3", { text: "Rules" }), rulesList,
      el("h3", { text: "Tokens" }),
      el("table", {}, [
        el("thead", {}, el("tr", {}, [el("th", { text: "Model" }), el("th", { text: "Before → After" }), el("th", { text: "Saved" })])),
        el("tbody", {}, tokenRows),
      ]),
      el("div", { class: "copy-row" }, copyButton(summaryText)),
    ]));
  } catch (e) { showError(e); }
}
