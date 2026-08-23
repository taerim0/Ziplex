// The topbar's fourth destination -- see app.js's header comment for the
// overall module split. Two settings so far: the GUI's own display
// language (i18n.js's getLang()/setLang(), localStorage-backed -- purely
// client-side, so no server round-trip the way the output folder below
// needs) and the default output folder new packs save to (settings.py's
// `output_dir`, GET/POST /api/settings) -- ahead of whatever else
// (per-project freshness checks, translating a *packed project's* own
// content -- see the roadmap items this GUI reorg is being driven by) end
// up living here later. Per-project folder pins aren't edited here at
// all: typing an explicit path in landing.js's renderPackHome() own
// "출력 경로" field is what sets one (see pack_service.start_pack_job())
// -- this page only ever touches the global fallback every *unpinned*
// project follows.
//
// Imports `route` from router.js, which itself imports renderOptions from
// here -- a real circular import, safe under ES modules because neither
// side uses the other's binding until well after both modules have
// finished evaluating (route() is only called from an event handler
// below, renderOptions() is only called from inside router.js's own
// route() function body, never at either module's top level).

import { app, nav, el, api, apiPost, browseButton } from "../app.js";
import { t, getLang, setLang, applyStaticI18n } from "../i18n.js";
import { route } from "../router.js";

export function renderOptions() {
  nav.classList.add("hidden");
  app.innerHTML = "";

  const outputDirInput = el("input", { type: "text", placeholder: t("options.outputDirPlaceholder") });
  const saveButton = el("button", { text: t("options.save") });
  const savedNote = el("span", { class: "muted hidden", text: t("options.saved") });
  const errorBox = el("div", { class: "error hidden" });

  // GUI display-language switcher (i18n.js) -- ko/en only for now, easy to
  // add more later since every string in this app is already keyed
  // through t(), not hardcoded per call site. Re-running route() after a
  // change re-renders whatever page is current (this one included) in the
  // new language; applyStaticI18n() separately re-translates index.html's
  // own static topbar/sidebar markup, which no render*() call touches.
  const langSelect = el("select", {}, [
    el("option", { value: "ko", text: "한국어" }),
    el("option", { value: "en", text: "English" }),
  ]);
  langSelect.value = getLang();
  langSelect.addEventListener("change", () => {
    setLang(langSelect.value);
    applyStaticI18n();
    route();
  });

  saveButton.addEventListener("click", async () => {
    savedNote.classList.add("hidden");
    errorBox.classList.add("hidden");
    saveButton.disabled = true;
    try {
      await apiPost("/api/settings", { output_dir: outputDirInput.value.trim() });
      savedNote.classList.remove("hidden");
    } catch (e) {
      errorBox.textContent = e.message;
      errorBox.classList.remove("hidden");
    } finally {
      saveButton.disabled = false;
    }
  });

  app.appendChild(el("div", { class: "landing" }, [
    el("div", { class: "card landing-intro" }, [
      el("h1", { text: t("nav.options") }),
    ]),
    el("div", { class: "card" }, [
      el("h2", { text: t("options.languageTitle") }),
      el("div", { class: "input-row" }, [langSelect]),
    ]),
    el("div", { class: "card" }, [
      el("h2", { text: t("options.outputDirTitle") }),
      el("p", { class: "muted", text: t("options.outputDirDescription") }),
      el("div", { class: "input-row" }, [outputDirInput, browseButton(outputDirInput)]),
      el("div", { class: "copy-row" }, [saveButton, savedNote]),
      errorBox,
    ]),
  ]));

  api("/api/settings").then(data => { outputDirInput.value = data.output_dir || ""; }).catch(() => {});
}
