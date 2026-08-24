// The topbar's fourth destination -- see app.js's header comment for the
// overall module split. Three settings so far: the GUI's own display
// language (i18n.js's getLang()/setLang(), localStorage-backed -- purely
// client-side, so no server round-trip the way the two below need), the
// Gemini API key (settings.py's `gemini_api_key`, GET/POST /api/settings
// -- a global credential, not a per-pack parameter, the same reasoning
// that put the output folder here instead of on the pack form), and the
// default output folder new packs save to (settings.py's `output_dir`) --
// ahead of whatever else (per-project freshness checks, translating a
// *packed project's* own content -- see the roadmap items this GUI reorg
// is being driven by) end up living here later. Per-project folder pins
// aren't edited here at all: typing an explicit path in landing.js's
// renderPackHome() own "출력 경로" field is what sets one (see
// pack_service.start_pack_job()) -- this page only ever touches the
// global fallback every *unpinned* project follows.
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

  // --- AI provider selector -------------------------------------------
  // Three providers today (llm.py's PROVIDERS registry, minus "mock" which
  // is a test-only seam never exposed here): Gemini, an OpenAI-compatible
  // family (covers real OpenAI and anything speaking its API -- Ollama, LM
  // Studio, vLLM, OpenRouter, a local Gemma/Llama served through any of
  // those), and Claude direct. The dropdown only ever changes which of the
  // three field groups below is visible; nothing is saved until the shared
  // save button below fires, and that save always sends llm_provider
  // alongside whichever group's fields are currently showing -- so
  // "switched provider, typed a key, saved" can never land in a state
  // where the key is stored but llm_provider still points at the old one.
  const providerSelect = el("select", {}, [
    el("option", { value: "gemini", text: t("options.providerGemini") }),
    el("option", { value: "openai", text: t("options.providerOpenai") }),
    el("option", { value: "claude", text: t("options.providerClaude") }),
  ]);

  // type="password" only masks these fields visually (shoulder-surfing) --
  // GET /api/settings still echoes the real values back in plain JSON so
  // they can be prefilled, same trust model as everything else this GUI
  // already assumes (single-user, 127.0.0.1-only -- see gui_server.py's
  // own docstring on why that binding choice is load-bearing here).
  const apiKeyInput = el("input", { type: "password", placeholder: t("options.apiKeyPlaceholder") });
  const geminiModelInput = el("input", { type: "text", placeholder: t("options.geminiModelPlaceholder") });
  const geminiFields = el("div", {}, [
    el("div", { class: "input-row" }, [apiKeyInput]),
    el("div", { class: "input-row" }, [geminiModelInput]),
  ]);

  const openaiApiKeyInput = el("input", { type: "password", placeholder: t("options.openaiApiKeyPlaceholder") });
  const openaiBaseUrlInput = el("input", { type: "text", placeholder: t("options.openaiBaseUrlPlaceholder") });
  const openaiModelInput = el("input", { type: "text", placeholder: t("options.openaiModelPlaceholder") });
  // One-click fill for the two local OpenAI-compatible servers people
  // actually run Gemma/Llama/etc. through -- OpenAIProvider already covers
  // both via base_url alone (llm.py has no per-tool special-casing), but a
  // human still has to know each tool's default port off the top of their
  // head to type it correctly. These just fill openaiBaseUrlInput with the
  // well-known default and hand focus to the model field, since the model
  // name is the one part that still varies by what's actually loaded there
  // (Ollama's own naming, e.g. "gemma2", vs. whatever LM Studio shows).
  // Never touch openaiApiKeyInput -- both servers usually run keyless, but
  // clearing a value someone deliberately typed (a reverse-proxied setup
  // with its own auth) would be a surprising side effect of a convenience
  // button.
  const ollamaPresetButton = el("button", { type: "button", class: "secondary", text: t("options.ollamaPreset") });
  const lmstudioPresetButton = el("button", { type: "button", class: "secondary", text: t("options.lmstudioPreset") });
  ollamaPresetButton.addEventListener("click", () => {
    openaiBaseUrlInput.value = "http://localhost:11434/v1";
    openaiModelInput.focus();
  });
  lmstudioPresetButton.addEventListener("click", () => {
    openaiBaseUrlInput.value = "http://localhost:1234/v1";
    openaiModelInput.focus();
  });
  const openaiFields = el("div", { class: "hidden" }, [
    el("div", { class: "input-row" }, [openaiApiKeyInput]),
    el("div", { class: "input-row" }, [openaiBaseUrlInput]),
    el("div", { class: "input-row" }, [ollamaPresetButton, lmstudioPresetButton]),
    el("div", { class: "input-row" }, [openaiModelInput]),
  ]);

  const claudeApiKeyInput = el("input", { type: "password", placeholder: t("options.claudeApiKeyPlaceholder") });
  const claudeModelInput = el("input", { type: "text", placeholder: t("options.claudeModelPlaceholder") });
  const claudeFields = el("div", { class: "hidden" }, [
    el("div", { class: "input-row" }, [claudeApiKeyInput]),
    el("div", { class: "input-row" }, [claudeModelInput]),
  ]);

  const providerDescription = el("p", { class: "muted", text: t("options.openaiDescription") });
  // Only Gemini's own description is the shared apiKeyDescription already
  // defined above the selector; OpenAI/Claude get their own paragraph,
  // swapped by showProvider() below the same way the field groups are.
  const geminiDescription = el("p", { class: "muted", text: t("options.apiKeyDescription") });
  const claudeDescription = el("p", { class: "muted hidden", text: t("options.claudeDescription") });
  providerDescription.classList.add("hidden");

  function showProvider(name) {
    geminiFields.classList.toggle("hidden", name !== "gemini");
    geminiDescription.classList.toggle("hidden", name !== "gemini");
    openaiFields.classList.toggle("hidden", name !== "openai");
    providerDescription.classList.toggle("hidden", name !== "openai");
    claudeFields.classList.toggle("hidden", name !== "claude");
    claudeDescription.classList.toggle("hidden", name !== "claude");
  }

  providerSelect.addEventListener("change", () => showProvider(providerSelect.value));

  const apiKeySaveButton = el("button", { text: t("options.save") });
  const apiKeySavedNote = el("span", { class: "muted hidden", text: t("options.saved") });
  const apiKeyErrorBox = el("div", { class: "error hidden" });

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

  apiKeySaveButton.addEventListener("click", async () => {
    apiKeySavedNote.classList.add("hidden");
    apiKeyErrorBox.classList.add("hidden");
    apiKeySaveButton.disabled = true;
    try {
      // Always sends llm_provider alongside whichever group's own fields
      // are currently visible -- see showProvider()'s own comment on why
      // that has to be one request, not two.
      const provider = providerSelect.value;
      const body = { llm_provider: provider };
      if (provider === "gemini") {
        body.gemini_api_key = apiKeyInput.value.trim();
        body.gemini_model = geminiModelInput.value.trim();
      } else if (provider === "openai") {
        body.openai_api_key = openaiApiKeyInput.value.trim();
        body.openai_base_url = openaiBaseUrlInput.value.trim();
        body.openai_model = openaiModelInput.value.trim();
      } else if (provider === "claude") {
        body.claude_api_key = claudeApiKeyInput.value.trim();
        body.claude_model = claudeModelInput.value.trim();
      }
      await apiPost("/api/settings", body);
      apiKeySavedNote.classList.remove("hidden");
    } catch (e) {
      apiKeyErrorBox.textContent = e.message;
      apiKeyErrorBox.classList.remove("hidden");
    } finally {
      apiKeySaveButton.disabled = false;
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
      el("h2", { text: t("options.providerTitle") }),
      el("p", { class: "muted", text: t("options.providerDescription") }),
      el("div", { class: "input-row" }, [providerSelect]),
      geminiDescription,
      geminiFields,
      providerDescription,
      openaiFields,
      claudeDescription,
      claudeFields,
      el("div", { class: "copy-row" }, [apiKeySaveButton, apiKeySavedNote]),
      apiKeyErrorBox,
    ]),
    el("div", { class: "card" }, [
      el("h2", { text: t("options.outputDirTitle") }),
      el("p", { class: "muted", text: t("options.outputDirDescription") }),
      el("div", { class: "input-row" }, [outputDirInput, browseButton(outputDirInput)]),
      el("div", { class: "copy-row" }, [saveButton, savedNote]),
      errorBox,
    ]),
  ]));

  api("/api/settings").then(data => {
    outputDirInput.value = data.output_dir || "";
    apiKeyInput.value = data.gemini_api_key || "";
    geminiModelInput.value = data.gemini_model || "";
    openaiApiKeyInput.value = data.openai_api_key || "";
    openaiBaseUrlInput.value = data.openai_base_url || "";
    openaiModelInput.value = data.openai_model || "";
    claudeApiKeyInput.value = data.claude_api_key || "";
    claudeModelInput.value = data.claude_model || "";

    const provider = data.llm_provider || "gemini";
    providerSelect.value = provider;
    showProvider(provider);
  }).catch(() => {});
}
