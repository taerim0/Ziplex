// The topbar's three non-Options global destinations -- see app.js's
// header comment for the overall module split. This file: renderHome()
// (route "/", the topbar brand/logo's own destination -- deliberately
// just a logo, no real content of its own), renderPackHome() (new-pack
// form only -- route "/pack", topbar's "프로젝트 패킹"), and renderCheck()
// (open-existing-project form + recent-projects list -- route "/check",
// topbar's "프로젝트 확인"). All three used to be one combined landing
// page at "/" until the topbar grew a dedicated slot for each; grouped
// together here (rather than one file per function) since they're still
// exactly the pre-project-loaded screens as a set.

import {
  app, nav, el, api, apiPost, getAif, getProject, getRecent, removeRecent,
  openProject, relativeTime, browseButton, browseAifButton, browseSaveButton,
  staleTooltip, renderStaleDetail,
} from "../app.js";
import { t } from "../i18n.js";

// Just the logo -- no content of its own yet. Exists as its own screen
// (rather than redirecting "/" straight to renderPackHome() or
// renderCheck()) purely because the topbar's brand link needs *something*
// to land on that isn't already one of the other three destinations'
// business.
export function renderHome() {
  nav.classList.add("hidden");
  app.innerHTML = "";
  app.appendChild(el("div", { class: "landing" }, [
    el("div", { class: "card landing-intro" }, [
      el("h1", { text: "📦 Ziplex" }),
      el("p", { text: t("home.tagline") }),
    ]),
  ]));
}

export function renderPackHome() {
  nav.classList.add("hidden");
  app.innerHTML = "";

  const packProjInput = el("input", { type: "text", placeholder: t("pack.form.projectPathPlaceholder") });
  const packOutInput = el("input", { type: "text", placeholder: t("pack.form.outputPathPlaceholder") });
  const noCacheInput = el("input", { type: "checkbox" });
  const noLlmInput = el("input", { type: "checkbox" });
  // Packed-CONTENT language (each file's summary/rules/AI guide, written by
  // the LLM itself, or -- with "LLM 사용 안 함" checked -- Ziplex's own fixed
  // structural-summary text) -- independent of the GUI's own display-
  // language switcher on the Options page (js/i18n.js, a fixed dictionary
  // translating only this app's own chrome, never anything an LLM writes).
  // "en" default/first option, matching llm.py's LANGUAGE_NAMES comment on
  // why English is both the default and the recommended choice.
  const packLangInput = el("select", {}, [
    el("option", { value: "en", text: t("pack.form.langEnglish") }),
    el("option", { value: "ko", text: t("pack.form.langKorean") }),
  ]);
  packLangInput.value = "en";
  const packError = el("div", { class: "error hidden" });
  const loadFilesButton = el("button", { class: "secondary", text: t("pack.form.loadFiles") });
  const fileListBox = el("div", { class: "hidden" });
  const packButton = el("button", { class: "hidden", text: t("pack.form.start") });

  let selectableCheckboxes = [];
  let dangerousCheckboxes = [];

  loadFilesButton.addEventListener("click", async () => {
    const project_path = packProjInput.value.trim();
    if (!project_path) { packProjInput.focus(); return; }
    packError.classList.add("hidden");
    packButton.classList.add("hidden");
    loadFilesButton.disabled = true;
    try {
      const data = await api("/api/select_files", { project_path });
      // settings.py's resolved default for *this* project (its own pin, or
      // the Options page's global default) -- shown as a placeholder, not
      // filled into the field's actual value, so leaving the field alone
      // still submits blank and re-resolves dynamically at pack time
      // (pack_service.start_pack_job()) rather than being treated as an
      // explicit path that would pin the project to whatever the default
      // just happened to be right now.
      if (data.default_output_path) packOutInput.placeholder = data.default_output_path;
      selectableCheckboxes = [];
      dangerousCheckboxes = [];
      fileListBox.innerHTML = "";

      if (!data.safe.length) {
        fileListBox.appendChild(el("p", { class: "muted", text: t("pack.form.noSafeFiles") }));
      } else {
        const selectAll = el("input", { type: "checkbox", checked: "checked" });
        selectAll.checked = true;
        selectAll.addEventListener("change", () => {
          for (const cb of selectableCheckboxes) cb.checked = selectAll.checked;
        });
        fileListBox.appendChild(el("label", { class: "file-checklist-row", style: "font-weight:600" }, [
          selectAll, el("span", { text: t("pack.form.allFiles", { n: data.safe.length }) }),
        ]));

        const list = el("div", { class: "file-checklist" });
        for (const name of data.safe) {
          const cb = el("input", { type: "checkbox", checked: "checked", "data-name": name });
          cb.checked = true;
          selectableCheckboxes.push(cb);
          list.appendChild(el("label", { class: "file-checklist-row" }, [cb, el("span", { text: name })]));
        }
        fileListBox.appendChild(list);
      }
      // Shown whenever there's anything selectable at all -- safe files,
      // or (an edge case, but a real one: a project that's nothing but
      // fixture/sample files) only a dangerous one a human can still
      // choose to override below.
      if (data.safe.length || data.dangerous.length) {
        packButton.classList.remove("hidden");
      }

      // Sensitive files used to just disappear here with a bare count --
      // "trust us" with no way back for a false positive (a fixture file,
      // a sample .env with placeholder values). Each one now shows *why*
      // it was flagged (scanner.py's scan_file() reason/matched line, not
      // the whole file -- enough to judge it without opening the file) and
      // an opt-in checkbox, unchecked by default and deliberately kept out
      // of `selectableCheckboxes` (so "전체" above can never sweep one in
      // by accident) -- see packButton's click handler below for how the
      // two lists merge back into one selected_files array.
      if (data.dangerous.length) {
        const box = el("div", { class: "dangerous-files" }, [
          el("p", { class: "muted", text: t("pack.form.dangerousDetected", { n: data.dangerous.length }) }),
        ]);
        for (const entry of data.dangerous) {
          const cb = el("input", { type: "checkbox", "data-name": entry.file });
          dangerousCheckboxes.push(cb);

          const detail = [el("div", { class: "dangerous-file-reason", text: entry.reason || t("pack.form.dangerousDefaultReason") })];
          if (entry.line && entry.matched_text != null) {
            detail.push(el("div", { class: "dangerous-file-line", text: t("pack.form.dangerousLine", { line: entry.line, text: entry.matched_text }) }));
          }

          box.appendChild(el("div", { class: "dangerous-file-row" }, [
            el("label", { class: "file-checklist-row" }, [cb, el("span", { text: entry.file })]),
            el("div", { class: "dangerous-file-detail" }, detail),
          ]));
        }
        fileListBox.appendChild(box);
      }
      fileListBox.classList.remove("hidden");
    } catch (e) {
      packError.textContent = e.message;
      packError.classList.remove("hidden");
    } finally {
      loadFilesButton.disabled = false;
    }
  });

  packButton.addEventListener("click", async () => {
    const project_path = packProjInput.value.trim();
    // Naming a dangerous file here is this screen's equivalent of the
    // CLI's review_dangerous_files() prompt -- packager.pack()'s
    // `preselected` handling trusts either list equally (see its own
    // comment), since ticking this box after seeing the same reason/
    // matched-line detail already *is* the human decision that prompt
    // represents, just made through a checkbox instead of a terminal menu.
    const selected_files = [...selectableCheckboxes, ...dangerousCheckboxes]
      .filter(cb => cb.checked).map(cb => cb.dataset.name);
    if (!selected_files.length) {
      packError.textContent = t("pack.form.noFilesSelected");
      packError.classList.remove("hidden");
      return;
    }
    packError.classList.add("hidden");
    packButton.disabled = true;
    try {
      const { job_id } = await apiPost("/api/pack", {
        project_path,
        output_path: packOutInput.value.trim(),
        no_cache: noCacheInput.checked,
        no_llm: noLlmInput.checked,
        selected_files,
        lang: packLangInput.value,
      });
      location.hash = `#/pack/${job_id}`;
    } catch (e) {
      packError.textContent = e.message;
      packError.classList.remove("hidden");
      packButton.disabled = false;
    }
  });

  const packCard = el("div", { class: "landing-pack card" }, [
    el("h1", { text: t("nav.pack") }),
    el("p", { class: "muted", text: t("pack.form.description") }),
    el("label", { text: t("pack.form.projectPathLabel") }),
    el("div", { class: "input-row" }, [packProjInput, browseButton(packProjInput)]),
    el("label", { text: t("pack.form.outputPathLabel") }),
    el("div", { class: "input-row" }, [packOutInput, browseSaveButton(packOutInput)]),
    el("label", { text: t("pack.form.langLabel"), style: "margin-top:14px" }),
    packLangInput,
    el("p", { class: "muted", text: t("pack.form.langHint") }),
    el("label", { style: "display:flex;align-items:center;gap:6px;margin-top:14px" }, [
      noCacheInput,
      el("span", { text: t("pack.form.noCacheLabel") }),
    ]),
    el("label", { style: "display:flex;align-items:center;gap:6px;margin-top:6px" }, [
      noLlmInput,
      el("span", { text: t("pack.form.noLlmLabel") }),
    ]),
    el("div", { class: "copy-row" }, loadFilesButton),
    fileListBox,
    el("div", { class: "copy-row" }, packButton),
    packError,
  ]);

  app.appendChild(el("div", { class: "landing" }, [packCard]));

  // prefill from server-side --project if nothing saved locally yet
  if (!getProject()) {
    api("/api/config").then(cfg => {
      if (cfg.project_path) packProjInput.value = cfg.project_path;
    }).catch(() => {});
  }
}

// The topbar's "프로젝트 확인" destination -- the open-existing-project form
// (aif.json path + optional project folder path, for the freshness check)
// plus the recent-projects list, both of which are about *reaching* an
// already-packed project rather than creating one, unlike renderPackHome()
// above. Recognition-rather-than-recall (Nielsen): a returning user
// shouldn't have to re-type or re-browse-to a path they've already opened.
export function renderCheck() {
  nav.classList.add("hidden");
  app.innerHTML = "";

  const aifInput = el("input", { type: "text", id: "aif-input", placeholder: t("check.form.aifPlaceholder"), value: getAif() });
  const projInput = el("input", { type: "text", id: "proj-input", placeholder: t("check.form.projectPlaceholder"), value: getProject() });

  const openCard = el("div", { class: "card landing-intro" }, [
    el("h1", { text: t("nav.check") }),
    el("p", { text: t("check.form.description") }),
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
