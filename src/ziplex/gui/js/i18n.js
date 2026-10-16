// GUI chrome translation (ko/en): a small hand-written dictionary, not a
// packed project's content (summaries stay in whatever language they were
// packed in). No imports -- the lowest-level module.
//
// getLang()/setLang() persist the choice in localStorage; t(key, vars)
// looks a string up (function entries interpolate vars); applyStaticI18n()
// re-translates index.html's static topbar/sidebar markup.
const LANG_KEY = "ziplex.lang";

export function getLang() {
  const saved = localStorage.getItem(LANG_KEY);
  return saved === "en" ? "en" : "ko"; // ko is the default -- always was, before this setting existed
}

export function setLang(lang) {
  localStorage.setItem(LANG_KEY, lang === "en" ? "en" : "ko");
}

// Each value is either a plain string, or a function taking a `vars`
// object for the handful of messages that interpolate a count/status code
// -- avoids needing even a tiny templating syntax for the ~15 strings
// that actually need one, at the cost of every call site passing vars as
// a plain object instead of a template literal.
const I18N = {
  ko: {
    "core.time.justNow": "방금",
    "core.time.minutesAgo": (v) => `${v.mins}분 전`,
    "core.time.hoursAgo": (v) => `${v.hours}시간 전`,
    "core.time.daysAgo": (v) => `${v.days}일 전`,
    "core.requestFailed": (v) => `요청 실패 (${v.status})`,
    "core.copy": "📋 복사",
    "core.copied": "복사됨 ✓",
    "core.loading": "불러오는 중...",
    "core.picker.unavailable": "선택 대화상자는 기본 실행 모드(네이티브 창)에서만 사용할 수 있습니다. --no-window로 실행 중이면 경로를 직접 입력해주세요.",
    "core.picker.browseFolder": "📁 찾아보기",
    "core.picker.browseFile": "📄 찾아보기",
    "core.stale.changed": (v) => `변경 ${v.n}`,
    "core.stale.added": (v) => `추가 ${v.n}`,
    "core.stale.removed": (v) => `삭제 ${v.n}`,
    "core.stale.detected": "변경 감지됨",
    "core.stale.badge": "⚠️ 변경됨",
    "core.stale.changedLabel": "변경됨",
    "core.stale.addedLabel": "추가됨",
    "core.stale.removedLabel": "삭제됨",
    "core.stale.more": (v) => `  ... 외 ${v.n}개 더`,

    "graph.more": (v) => `+${v.n}개 더`,
    "graph.tree.cycleSkipped": "(순환 참조 → 생략)",
    "graph.tree.alreadyShown": "(다른 곳에 이미 표시됨 → 생략)",
    "graph.tree.empty": "표시할 파일이 없습니다.",
    "graph.tree.cyclesSummary": (v) => `🔁 순환 참조 ${v.n}건 발견됨 — 아래 트리에서 강조 표시됨`,
    "graph.editor.searchPlaceholder": "🔍 파일 검색...",
    "graph.editor.noMatch": "일치하는 파일 없음",
    "graph.editor.selectPrompt": "왼쪽에서 파일을 선택하세요.",
    "graph.editor.noDependencies": "(이 파일이 의존하는 대상 없음)",
    "graph.editor.dependentsOf": (v) => `이 파일에 의존하는 파일 ${v.n}개: ${v.list}`,

    "nav.check": "📂 프로젝트 확인",
    "nav.options": "⚙️ 옵션",
    "nav.changeProject": "📂 프로젝트 변경",
    "topbar.langLabel": "언어",
    "topbar.themeLabel": "테마",
    "nav.overview": "📊 개요",
    "nav.files": "📄 파일",
    "nav.relationships": "🔗 관계",
    "nav.search": "🔍 검색",

    "home.packHintTitle": "aif.json 만들기",
    "home.packHint": "이 GUI는 이미 pack된 프로젝트를 읽기 전용으로 보여줍니다. 먼저 터미널에서 실행하세요:",
    "home.packHintThen": "그다음 \"프로젝트 확인\"에서 생성된 aif.json을 여세요 (기본 위치: <프로젝트>/.ziplex/<이름>.json). ziplex index는 그래프 인덱스만 만듭니다.",
    "check.form.packHint": "아직 aif.json이 없다면 먼저 터미널에서 ziplex pack <프로젝트>를 실행하세요.",
    "relationships.title": "파일 관계",
    "relationships.help": "▶ 를 클릭해 하위 트리를 접거나 펼치세요. 파일 이름을 클릭하면 그 파일의 의존성 그래프가 열립니다 (읽기 전용).",
    "relationships.backToTree": "← 트리로 돌아가기",
    "home.tagline": "로컬 프로젝트를 압축된 컨텍스트로 요약해, 원본 대신 AI에게 보여주는 도구입니다.",

    "check.form.aifPlaceholder": "예: .ziplex/my-project.json",
    "check.form.projectPlaceholder": "예: C:\\path\\to\\my-project (선택, 최신 여부 확인용)",
    "check.form.description": "이미 pack된 프로젝트를 둘러보고, 필요한 부분을 복사해 다른 AI 챗에 붙여넣으세요.",
    "check.form.aifLabel": "aif.json 경로",
    "check.form.projectLabel": "프로젝트 폴더 경로 (선택)",
    "check.form.open": "열기",
    "check.freshness.stale": (v) => `⚠️ ${v.n}개 변경`,
    "check.freshness.fresh": "✅ 최신",
    "check.recentTitle": "최근 프로젝트",

    "options.themeDark": "다크",
    "options.themeLight": "라이트",
    "options.save": "저장",
    "options.saved": "저장됨",
    "options.apiKeyDescription": "패킹 시 요약/코딩 룰/AI 가이드 생성에 사용할 Gemini API 키입니다. 비워두면 .env의 GEMINI_API_KEY를 그대로 사용합니다 (여기서 설정하면 이 값이 우선함).",
    "options.apiKeyPlaceholder": "비우면 .env의 GEMINI_API_KEY 사용",
    "options.apiKeyLabel": "API 키",
    "options.geminiModelLabel": "모델 (선택)",
    "options.geminiModelPlaceholder": "비우면 gemini-flash-latest 사용 (과부하 시 gemini-3.5-flash 같은 고정 버전 권장)",
    "options.geminiModelHint": "비워두면 최신 Flash 모델을 자동으로 가리키는 gemini-flash-latest를 사용합니다. 이 이름은 Google이 계속 최신 버전으로 갱신하는 \"떠다니는\" 별칭이라 트래픽이 몰리는 시간대엔 503 오류가 반복될 수 있어요 — 그럴 땐 gemini-2.5-flash처럼 특정 버전을 직접 적어 고정하세요.",

    "options.providerTitle": "AI 프로바이더",
    "options.providerDescription": "패킹 시 요약/코딩 룰/AI 가이드 생성에 사용할 AI를 선택하세요.",
    "options.providerGemini": "Gemini",
    "options.providerOpenai": "OpenAI 호환 (OpenAI, Ollama, LM Studio, Gemma 등)",
    "options.providerClaude": "Claude (Anthropic)",
    "options.openaiDescription": "OpenAI, Ollama, LM Studio, vLLM, OpenRouter 등 OpenAI 호환 API를 쓰는 모든 서비스에 이 설정이 적용됩니다. 로컬 서버는 보통 API 키가 필요 없습니다.",
    "options.openaiApiKeyPlaceholder": "로컬 서버는 보통 비워둬도 됨",
    "options.openaiBaseUrlLabel": "서버 주소 (base URL)",
    "options.openaiBaseUrlPlaceholder": "예: http://localhost:11434/v1 (Ollama), http://localhost:1234/v1 (LM Studio) — 비우면 OpenAI 공식 서버",
    "options.ollamaPreset": "Ollama 기본값",
    "options.lmstudioPreset": "LM Studio 기본값",
    "options.openaiModelLabel": "모델 이름 (필수)",
    "options.openaiModelPlaceholder": "예: gemma2, llama3.1, gpt-4o-mini",
    "options.openaiModelHint": "지금 연결한 서버가 실제로 쓰는 모델 이름을 그대로 입력해야 합니다 — 정해진 기본값이 없어서 비워두면 실패해요. 진짜 OpenAI라면 gpt-4o-mini처럼, Ollama/LM Studio라면 그 프로그램의 모델 목록에 뜨는 이름 그대로(예: gemma2) 적으면 됩니다.",
    "options.claudeDescription": "Anthropic Claude API 키와 모델입니다.",
    "options.claudeApiKeyPlaceholder": "Claude API 키",
    "options.claudeModelLabel": "모델 (선택)",
    "options.claudeModelPlaceholder": "비우면 claude-sonnet-4-5 사용",
    "options.claudeModelHint": "비워두면 기본 모델(claude-sonnet-4-5)을 사용합니다. claude-opus-4-5처럼 다른 Claude 모델을 쓰고 싶을 때만 직접 입력하세요.",

    "overview.untitled": "(제목 없음)",
    "overview.fileCount": (v) => `파일 ${v.n}개`,
    "overview.techStackHeading": "기술 스택",
    "overview.techStackDeps": (v) => `(${v.n}개)`,
    "overview.approxTag": " (근사치)",

    "files.searchPlaceholder": "파일명/요약 검색...",
    "files.rootFolder": "(최상위)",
    "files.noResults": "일치하는 파일이 없습니다.",
    "files.hideSummaries": "요약 전체 숨기기",
    "files.showSummaries": "요약 전체 표시",

    "fileDetail.none": "(없음)",
    "fileDetail.dependents": "Dependents (이 파일에 의존하는 파일)",
    "fileDetail.blastRadius": "Blast radius (이 파일 변경 시 영향받는 전체 범위)",
    "fileDetail.noContent": "(내용 없음)",
    "fileDetail.copyAll": "📋 전체 복사",
    "fileDetail.includeTextRefs": "텍스트 언급(파일명만 일치)도 포함",

    "search.patternPlaceholder": "정규식 패턴 (예: TODO|FIXME)",
    "search.searching": "검색 중...",
    "search.noResults": "검색 결과 없음",
    "search.needsProject": "검색하려면 프로젝트 폴더가 필요합니다 -- 프로젝트 확인 화면에서 폴더 경로와 함께 다시 열어 주세요",
    "search.button": "검색",
  },
  en: {
    "core.time.justNow": "just now",
    "core.time.minutesAgo": (v) => `${v.mins}m ago`,
    "core.time.hoursAgo": (v) => `${v.hours}h ago`,
    "core.time.daysAgo": (v) => `${v.days}d ago`,
    "core.requestFailed": (v) => `Request failed (${v.status})`,
    "core.copy": "📋 Copy",
    "core.copied": "Copied ✓",
    "core.loading": "Loading...",
    "core.picker.unavailable": "Native dialogs are only available in the default (native window) mode. If you're running with --no-window, type the path directly.",
    "core.picker.browseFolder": "📁 Browse",
    "core.picker.browseFile": "📄 Browse",
    "core.stale.changed": (v) => `${v.n} changed`,
    "core.stale.added": (v) => `${v.n} added`,
    "core.stale.removed": (v) => `${v.n} removed`,
    "core.stale.detected": "Changes detected",
    "core.stale.badge": "⚠️ Changed",
    "core.stale.changedLabel": "Changed",
    "core.stale.addedLabel": "Added",
    "core.stale.removedLabel": "Removed",
    "core.stale.more": (v) => `  ... +${v.n} more`,

    "graph.more": (v) => `+${v.n} more`,
    "graph.tree.cycleSkipped": "(circular reference → omitted)",
    "graph.tree.alreadyShown": "(already shown elsewhere → omitted)",
    "graph.tree.empty": "No files to show.",
    "graph.tree.cyclesSummary": (v) => `🔁 ${v.n} circular reference(s) found — highlighted in the tree below`,
    "graph.editor.searchPlaceholder": "🔍 Search files...",
    "graph.editor.noMatch": "No matching files",
    "graph.editor.selectPrompt": "Select a file on the left.",
    "graph.editor.noDependencies": "(this file has no dependencies)",
    "graph.editor.dependentsOf": (v) => `${v.n} file(s) depend on this: ${v.list}`,

    "nav.check": "📂 Check Project",
    "nav.options": "⚙️ Options",
    "nav.changeProject": "📂 Change Project",
    "topbar.langLabel": "Language",
    "topbar.themeLabel": "Theme",
    "nav.overview": "📊 Overview",
    "nav.files": "📄 Files",
    "nav.relationships": "🔗 Relationships",
    "nav.search": "🔍 Search",

    "home.packHintTitle": "Making an aif.json",
    "home.packHint": "This GUI is a read-only viewer for an already-packed project. Run this in a terminal first:",
    "home.packHintThen": "Then open the resulting aif.json from \"Check Project\" (default: <project>/.ziplex/<name>.json). ziplex index builds only the graph index.",
    "check.form.packHint": "No aif.json yet? Run ziplex pack <project> in a terminal first.",
    "relationships.title": "File relationships",
    "relationships.help": "Click ▶ to expand/collapse a subtree. Click a file's name to open its dependency graph (read-only).",
    "relationships.backToTree": "← Back to tree",
    "home.tagline": "Summarizes a local project into compressed context, for an AI to read instead of the original.",

    "check.form.aifPlaceholder": "e.g. .ziplex/my-project.json",
    "check.form.projectPlaceholder": "e.g. C:\\path\\to\\my-project (optional, for the freshness check)",
    "check.form.description": "Browse an already-packed project and copy what you need into a separate AI chat.",
    "check.form.aifLabel": "aif.json path",
    "check.form.projectLabel": "Project folder path (optional)",
    "check.form.open": "Open",
    "check.freshness.stale": (v) => `⚠️ ${v.n} changed`,
    "check.freshness.fresh": "✅ Up to date",
    "check.recentTitle": "Recent Projects",

    "options.themeDark": "Dark",
    "options.themeLight": "Light",
    "options.save": "Save",
    "options.saved": "Saved",
    "options.apiKeyDescription": "The Gemini API key used to generate summaries/coding rules/AI guide when packing. Leave blank to use .env's GEMINI_API_KEY as-is (setting one here takes priority over it).",
    "options.apiKeyPlaceholder": "Leave blank to use .env's GEMINI_API_KEY",
    "options.apiKeyLabel": "API Key",
    "options.geminiModelLabel": "Model (optional)",
    "options.geminiModelPlaceholder": "Leave blank for gemini-flash-latest (a pinned version like gemini-3.5-flash is recommended if it's overloaded)",
    "options.geminiModelHint": "Leave blank to use gemini-flash-latest, an alias Google keeps pointing at whatever its newest Flash model is. Because it floats, it can hit repeated 503 errors during high-traffic periods — pin a specific version like gemini-2.5-flash here if that happens.",

    "options.providerTitle": "AI Provider",
    "options.providerDescription": "Choose which AI generates summaries/coding rules/the AI guide when packing.",
    "options.providerGemini": "Gemini",
    "options.providerOpenai": "OpenAI-compatible (OpenAI, Ollama, LM Studio, Gemma, ...)",
    "options.providerClaude": "Claude (Anthropic)",
    "options.openaiDescription": "Applies to OpenAI itself and anything speaking its API (Ollama, LM Studio, vLLM, OpenRouter, ...). A local server usually needs no API key at all.",
    "options.openaiApiKeyPlaceholder": "Usually blank for a local server",
    "options.openaiBaseUrlLabel": "Base URL",
    "options.openaiBaseUrlPlaceholder": "e.g. http://localhost:11434/v1 (Ollama), http://localhost:1234/v1 (LM Studio) — blank defaults to OpenAI's own server",
    "options.ollamaPreset": "Ollama defaults",
    "options.lmstudioPreset": "LM Studio defaults",
    "options.openaiModelLabel": "Model name (required)",
    "options.openaiModelPlaceholder": "e.g. gemma2, llama3.1, gpt-4o-mini",
    "options.openaiModelHint": "Type exactly the model name the server you're pointing at actually serves — there's no default, so leaving this blank fails. For real OpenAI that's something like gpt-4o-mini; for Ollama/LM Studio, use the exact name shown in that tool's own model list (e.g. gemma2).",
    "options.claudeDescription": "Your Anthropic Claude API key and model.",
    "options.claudeApiKeyPlaceholder": "Claude API key",
    "options.claudeModelLabel": "Model (optional)",
    "options.claudeModelPlaceholder": "Leave blank for claude-sonnet-4-5",
    "options.claudeModelHint": "Leave blank to use the default model (claude-sonnet-4-5). Only set this if you want a different Claude model, e.g. claude-opus-4-5.",

    "overview.untitled": "(untitled)",
    "overview.fileCount": (v) => `${v.n} files`,
    "overview.techStackHeading": "Tech Stack",
    "overview.techStackDeps": (v) => `(${v.n})`,
    "overview.approxTag": " (approx.)",

    "files.searchPlaceholder": "Search filename/summary...",
    "files.rootFolder": "(root)",
    "files.noResults": "No matching files.",
    "files.hideSummaries": "Hide all summaries",
    "files.showSummaries": "Show all summaries",

    "fileDetail.none": "(none)",
    "fileDetail.dependents": "Dependents (files that depend on this one)",
    "fileDetail.blastRadius": "Blast radius (everything affected if this file changes)",
    "fileDetail.noContent": "(no content)",
    "fileDetail.copyAll": "📋 Copy All",
    "fileDetail.includeTextRefs": "Include text-only mentions (filename match, not a real import)",

    "search.patternPlaceholder": "Regex pattern (e.g. TODO|FIXME)",
    "search.searching": "Searching...",
    "search.noResults": "No results",
    "search.needsProject": "Search needs the project folder -- reopen this project with its folder path on the Open Project screen",
    "search.button": "Search",
  },
};

// key not found in the current language falls back to Korean (the
// dictionary's always-complete language, since it's what every string in
// this codebase started as) rather than the bare key -- a translation gap
// should degrade to "shows Korean" instead of "shows core.some.key" to a
// human who has no idea what that means.
export function t(key, vars) {
  const dict = I18N[getLang()] || I18N.ko;
  const entry = key in dict ? dict[key] : I18N.ko[key];
  if (entry === undefined) return key;
  return typeof entry === "function" ? entry(vars || {}) : entry;
}

// index.html's sidebar/topbar links are static markup, not built by any
// render*() -- nothing re-translates them on navigation the way t() calls
// inside a render*() body do automatically. Each carries a data-i18n
// attribute naming its own key; called once on DOMContentLoaded and again
// whenever the language switcher changes (see app.js's
// initTopbarToggles()).
export function applyStaticI18n() {
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.dataset.i18n);
  }
  // Same idea, for an attribute instead of visible text -- added for the
  // topbar's #lang-toggle/#theme-toggle groups' own aria-label (a real gap
  // found by code review: every other user-facing string in this app goes
  // through t(), but a group's accessible name isn't textContent, so the
  // [data-i18n] loop above never touched it, leaving it hardcoded English
  // regardless of getLang()). Generic by attribute name rather than
  // hardcoded to aria-label specifically, in case a future static element
  // needs a different translated attribute (title, placeholder, ...).
  for (const el of document.querySelectorAll("[data-i18n-aria]")) {
    el.setAttribute("aria-label", t(el.dataset.i18nAria));
  }
}
