// The topbar's Options page: which LLM provider/model/key `ziplex pack`
// uses (settings.py, via GET/POST /api/settings). Language and theme live
// in the topbar toggles instead.
import { app, nav, el, api, apiPost } from "../app.js";
import { t } from "../i18n.js";

export function renderOptions() {
  nav.classList.add("hidden");
  app.innerHTML = "";


  // --- AI provider selector -------------------------------------------
  // Three providers today (the LLM layer's providers, minus "mock" which
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
  // A label above each field (not just placeholder text, which vanishes the
  // moment something's typed) plus a persistent hint paragraph under the
  // model field specifically -- reported directly: the model field's own
  // explanation used to live only in its placeholder, cramped and gone
  // as soon as you started typing, with no way to re-read *why* you might
  // want to change it (the gemini-flash-latest floating-alias/503 story)
  // once you had. geminiModelLabel/openaiModelLabel/openaiBaseUrlLabel/
  // claudeModelLabel already existed in i18n.js's own dictionary but were
  // never actually rendered anywhere until now.
  const geminiFields = el("div", {}, [
    el("label", { text: t("options.apiKeyLabel") }),
    el("div", { class: "input-row" }, [apiKeyInput]),
    el("label", { text: t("options.geminiModelLabel"), style: "margin-top:14px" }),
    el("div", { class: "input-row" }, [geminiModelInput]),
    el("p", { class: "muted", text: t("options.geminiModelHint") }),
  ]);

  const openaiApiKeyInput = el("input", { type: "password", placeholder: t("options.openaiApiKeyPlaceholder") });
  const openaiBaseUrlInput = el("input", { type: "text", placeholder: t("options.openaiBaseUrlPlaceholder") });
  const openaiModelInput = el("input", { type: "text", placeholder: t("options.openaiModelPlaceholder") });
  // One-click fill for the two local OpenAI-compatible servers people
  // actually run Gemma/Llama/etc. through -- OpenAIProvider already covers
  // both via base_url alone (no per-tool special-casing), but a
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
    el("label", { text: t("options.apiKeyLabel") }),
    el("div", { class: "input-row" }, [openaiApiKeyInput]),
    el("label", { text: t("options.openaiBaseUrlLabel"), style: "margin-top:14px" }),
    el("div", { class: "input-row" }, [openaiBaseUrlInput]),
    el("div", { class: "input-row" }, [ollamaPresetButton, lmstudioPresetButton]),
    el("label", { text: t("options.openaiModelLabel"), style: "margin-top:14px" }),
    el("div", { class: "input-row" }, [openaiModelInput]),
    el("p", { class: "muted", text: t("options.openaiModelHint") }),
  ]);

  const claudeApiKeyInput = el("input", { type: "password", placeholder: t("options.claudeApiKeyPlaceholder") });
  const claudeModelInput = el("input", { type: "text", placeholder: t("options.claudeModelPlaceholder") });
  const claudeFields = el("div", { class: "hidden" }, [
    el("label", { text: t("options.apiKeyLabel") }),
    el("div", { class: "input-row" }, [claudeApiKeyInput]),
    el("label", { text: t("options.claudeModelLabel"), style: "margin-top:14px" }),
    el("div", { class: "input-row" }, [claudeModelInput]),
    el("p", { class: "muted", text: t("options.claudeModelHint") }),
  ]);

  const providerDescription = el("p", { class: "muted", text: t("options.openaiDescription") });
  // Only Gemini's own description is the shared apiKeyDescription already
  // defined above the selector; OpenAI/Claude get their own paragraph,
  // swapped by showProvider() below the same way the field groups are.
  const geminiDescription = el("p", { class: "muted", text: t("options.apiKeyDescription") });
  const claudeDescription = el("p", { class: "muted hidden", text: t("options.claudeDescription") });
  providerDescription.classList.add("hidden");

  // One shared {provider name -> its own fields/description/save payload}
  // map -- showProvider() (visibility toggle) and the save handler below
  // both derive from this instead of each independently hardcoding the
  // same three-way gemini/openai/claude branch, a real drift risk found by
  // code review (a provider added to one branch but not the other would
  // silently show fields that save nothing, or vice versa). Each
  // provider's own field *elements* (labels, hints, preset buttons) still
  // stay fully bespoke above -- this only consolidates "which provider
  // owns which fields," not the fields' own genuinely different shapes.
  const providerGroups = {
    gemini: {
      fields: geminiFields,
      description: geminiDescription,
      collectBody: () => ({
        gemini_api_key: apiKeyInput.value.trim(),
        gemini_model: geminiModelInput.value.trim(),
      }),
    },
    openai: {
      fields: openaiFields,
      description: providerDescription,
      collectBody: () => ({
        openai_api_key: openaiApiKeyInput.value.trim(),
        openai_base_url: openaiBaseUrlInput.value.trim(),
        openai_model: openaiModelInput.value.trim(),
      }),
    },
    claude: {
      fields: claudeFields,
      description: claudeDescription,
      collectBody: () => ({
        claude_api_key: claudeApiKeyInput.value.trim(),
        claude_model: claudeModelInput.value.trim(),
      }),
    },
  };

  function showProvider(name) {
    for (const [key, group] of Object.entries(providerGroups)) {
      const active = key === name;
      group.fields.classList.toggle("hidden", !active);
      group.description.classList.toggle("hidden", !active);
    }
  }

  providerSelect.addEventListener("change", () => showProvider(providerSelect.value));

  const apiKeySaveButton = el("button", { text: t("options.save") });
  const apiKeySavedNote = el("span", { class: "muted hidden", text: t("options.saved") });
  const apiKeyErrorBox = el("div", { class: "error hidden" });

  apiKeySaveButton.addEventListener("click", async () => {
    apiKeySavedNote.classList.add("hidden");
    apiKeyErrorBox.classList.add("hidden");
    apiKeySaveButton.disabled = true;
    try {
      // Always sends llm_provider alongside whichever group's own fields
      // are currently visible -- see providerGroups' own comment on why
      // that has to be one request, not two.
      const provider = providerSelect.value;
      const group = providerGroups[provider];
      const body = { llm_provider: provider, ...(group ? group.collectBody() : {}) };
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
  ]));

  api("/api/settings").then(data => {
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
