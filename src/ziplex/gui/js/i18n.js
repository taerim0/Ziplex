// GUI display-language switcher -- Korean/English translation of the
// GUI's own chrome (buttons, labels, messages), *not* a packed project's
// content (summaries/AI guide/rules stay whatever language they were
// packed in; that's a separate, still-open item -- see the roadmap
// memory). Deliberately a small hand-written dictionary + lookup, not a
// runtime AI/translation-API call: the GUI's own text is a small, fixed,
// already-known set of strings, so translating it once at dev time (like
// this file) costs nothing per view, needs no API key, and works with
// --no-window/no network exactly like everything else in this frontend.
//
// Real ES modules (native browser support, `<script type="module">` in
// index.html -- no bundler, still no build step) -- this has no imports
// of its own, since it's the lowest layer everything else depends on.
//
// This file: LANG_KEY/getLang()/setLang() (localStorage-backed, same
// pattern as app.js's aif_path/project_path -- a client-side display
// preference that lives alongside the other browser-only state rather
// than in settings.py's per-user config file, which only ever holds
// things the *backend* needs to know on its own), the I18N dictionary
// (ko/en), t(key, vars) (the lookup + optional interpolation), and
// applyStaticI18n() (for index.html's own static markup -- the
// sidebar/topbar links aren't rendered by any render*() function, so
// nothing else in this app would ever re-translate them on a language
// switch without this). Not persisted to the backend -- landing.js's own
// pack-start request is the one place getLang() rides along as a plain
// request field (`progress_lang`, so a pack job's own progress log
// matches this same language -- see packager.pack()'s param of the same
// name), a one-off value for that single call, not a stored preference.

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
    "graph.editor.unlink": "끊기",
    "graph.editor.noDependencies": "(이 파일이 의존하는 대상 없음)",
    "graph.editor.dependentsOf": (v) => `이 파일에 의존하는 파일 ${v.n}개: ${v.list}`,
    "graph.editor.linkSearchPlaceholder": "🔍 연결할 파일 검색...",
    "graph.editor.addLink": "+ 연결 추가",

    "pack.status.running": "진행 중...",
    "pack.status.reviewing": "검토 대기 중",
    "pack.status.error": "오류",
    "pack.status.done": "완료",
    "pack.stopSave": "저장 후 취소",
    "pack.stopDiscard": "그냥 취소",
    "pack.confirmStopSave": "지금까지 진행 상황을 저장하고 중단할까요? 다음에 같은 프로젝트를 pack하면 이어서 진행됩니다.",
    "pack.confirmStopDiscard": "저장하지 않고 중단할까요? 지금까지 진행 상황이 모두 사라집니다.",
    "pack.guard.runningModalMessage": "패킹이 아직 진행 중입니다. 지금 벗어나면 패킹이 중단됩니다.",
    "pack.guard.stay": "머무르기",
    "pack.guard.confirm": "확인",
    "pack.guard.cancel": "취소",
    "pack.title": "패킹 진행 상황",
    "pack.log": "로그",
    "pack.saved": (v) => `저장됨: ${v.path}`,
    "pack.openResult": "결과 열기",
    "pack.unknownError": "알 수 없는 오류",
    "pack.retry": "🔄 다시 시도",
    "pack.backToPackForm": "새 패킹 화면으로",
    "pack.review.moreSignatures": (v) => `+ ${v.n}개 더`,
    "pack.review.deleteRule": "삭제",
    "pack.review.newRulePlaceholder": "새 룰 추가",
    "pack.review.addRule": "추가",
    "pack.review.backToTree": "← 트리로 돌아가기",
    "pack.review.noNeedsReview": "검토가 필요한 낮은 신뢰도 요약이 없습니다.",
    "pack.review.submit": "완료 및 저장",
    "pack.review.cancel": "취소",
    "pack.review.confirmCancel": "검토 중인 내용을 취소하고 버릴까요? 저장되지 않은 편집 내용이 모두 사라집니다.",
    // The two confirmDiscardReview() button labels -- deliberately separate
    // keys from pack.guard.stay/pack.review.cancel (see pack.js's own
    // comment on that function) rather than reusing either: a bare "취소"
    // on this specific modal didn't say what it actually does (leaves the
    // review screen, discarding every edit) -- reported directly.
    "pack.review.leaveWithoutSaving": "저장하지 않고 이동",
    "pack.review.stayCancel": "취소 (머무르기)",
    "pack.review.projectName": "프로젝트 이름",
    "pack.review.aiGuide": "AI 가이드",
    "pack.review.codingRules": "코딩 룰",
    "pack.review.folderSummariesHeader": "폴더 Summary",
    "pack.review.fileRelations": "파일 관계",
    "pack.review.relationsHelp": "전체 의존성 트리입니다 (▶ 를 클릭해 하위 트리를 접거나 펼치세요). 수정하고 싶은 파일 이름을 클릭하면 그 파일의 관계 편집 화면이 열립니다 -- 그래프의 다른 파일 노드를 클릭해 이동하거나, \"끊기\"로 의존성을 제거하거나, 검색창에 파일명을 입력해 새 의존성을 추가할 수 있습니다. 외부 패키지(📦)는 읽기 전용입니다.",
    "pack.review.needsReviewHeader": (v) => `⚠️ 검토 필요 (${v.n}개)`,
    "pack.review.autoKeptHeader": (v) => `자동 승인됨 (${v.n}개, 필요 시 수정 가능)`,

    "nav.pack": "📦 프로젝트 패킹",
    "nav.check": "📂 프로젝트 확인",
    "nav.options": "⚙️ 옵션",
    "nav.changeProject": "📂 프로젝트 변경",
    "nav.overview": "📊 Overview",
    "nav.files": "📄 Files",
    "nav.relationships": "🔗 Relationships",
    "nav.search": "🔍 Search",

    "home.tagline": "로컬 프로젝트를 압축된 컨텍스트로 요약해, 원본 대신 AI에게 보여주는 도구입니다.",

    "pack.form.projectPathPlaceholder": "예: C:\\path\\to\\my-project",
    "pack.form.outputPathPlaceholder": "선택. 비우면 result/<프로젝트명>.json",
    "pack.form.loadFiles": "파일 목록 불러오기",
    "pack.form.start": "패킹 시작",
    "pack.form.noSafeFiles": "선택 가능한 안전한 파일이 없습니다.",
    "pack.form.allFiles": (v) => `전체 ${v.n}개 파일`,
    "pack.form.dangerousDetected": (v) => `⚠️ 민감 파일 ${v.n}개 감지됨 (기본 제외 -- 아래에서 확인 후 필요하면 포함)`,
    "pack.form.dangerousExplain": "체크한 파일은 원본 내용이 그대로 패킹 결과(aif.json)에 포함됩니다. 아래 감지 사유와 일치한 줄을 확인한 뒤, 실제로 민감한 정보가 아님을 확인한 경우에만 포함하세요.",
    "pack.form.dangerousSelectAll": "⚠️ 감지된 파일 전체 포함",
    "pack.form.dangerousDefaultReason": "민감 정보로 추정됨",
    "pack.form.dangerousLine": (v) => `${v.line}번째 줄: ${v.text}`,
    "pack.form.noFilesSelected": "선택된 파일이 없습니다",
    "pack.form.description": "파일을 선택해 LLM 요약을 생성한 뒤, 저장 전에 검토/수정할 수 있습니다 (CLI의 대화형 pack과 동일).",
    "pack.form.projectPathLabel": "프로젝트 폴더 경로",
    "pack.form.outputPathLabel": "출력 경로 (선택)",
    "pack.form.noCacheLabel": "이전 pack 캐시 무시 (변경 없는 파일도 전체 재요약)",
    "pack.form.noLlmLabel": "LLM 사용 안 함 (GEMINI_API_KEY 불필요 -- 요약은 시그니처/의존성만으로 자동 생성, 코딩 룰/AI 가이드 생략)",
    "pack.form.langLabel": "패킹 언어",
    "pack.form.langHint": "파일별 summary/coding rules/AI 가이드가 작성될 언어입니다 (이 GUI 화면 자체의 언어와는 별개). 영어가 기본값이자 권장값입니다.",
    "pack.form.langEnglish": "English (권장)",
    "pack.form.langKorean": "한국어",

    "check.form.aifPlaceholder": "예: result/my-project.json",
    "check.form.projectPlaceholder": "예: C:\\path\\to\\my-project (선택, 최신 여부 확인용)",
    "check.form.description": "이미 pack된 프로젝트를 둘러보고, 필요한 부분을 복사해 다른 AI 챗에 붙여넣으세요.",
    "check.form.aifLabel": "aif.json 경로",
    "check.form.projectLabel": "프로젝트 폴더 경로 (선택)",
    "check.form.open": "열기",
    "check.freshness.stale": (v) => `⚠️ ${v.n}개 변경`,
    "check.freshness.fresh": "✅ 최신",
    "check.recentTitle": "최근 프로젝트",

    "options.languageTitle": "표시 언어",
    "options.outputDirPlaceholder": "비우면 result/<프로젝트명>.json (Ziplex 설치 폴더 내부)",
    "options.save": "저장",
    "options.saved": "저장됨",
    "options.apiKeyTitle": "Gemini API 키",
    "options.apiKeyDescription": "패킹 시 요약/코딩 룰/AI 가이드 생성에 사용할 Gemini API 키입니다. 비워두면 .env의 GEMINI_API_KEY를 그대로 사용합니다 (여기서 설정하면 이 값이 우선함).",
    "options.apiKeyPlaceholder": "비우면 .env의 GEMINI_API_KEY 사용",
    "options.geminiModelLabel": "모델",
    "options.geminiModelPlaceholder": "비우면 gemini-flash-latest 사용 (과부하 시 gemini-3.5-flash 같은 고정 버전 권장)",
    "options.outputDirTitle": "기본 저장 폴더",
    "options.outputDirDescription": "새로 패킹하는 프로젝트가 기본으로 저장될 폴더입니다. 패킹 화면의 \"출력 경로\"에 직접 경로를 입력한 프로젝트는 이 설정 대신 그 경로를 계속 기억해 사용합니다.",

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
    "options.openaiModelLabel": "모델",
    "options.openaiModelPlaceholder": "예: gemma2, llama3.1, gpt-4o-mini",
    "options.claudeDescription": "Anthropic Claude API 키와 모델입니다.",
    "options.claudeApiKeyPlaceholder": "Claude API 키",
    "options.claudeModelLabel": "모델",
    "options.claudeModelPlaceholder": "비우면 claude-sonnet-4-5 사용",

    "overview.untitled": "(제목 없음)",
    "overview.fileCount": (v) => `파일 ${v.n}개`,
    "overview.techStackHeading": "기술 스택",
    "overview.techStackDeps": (v) => `(${v.n}개)`,

    "files.searchPlaceholder": "파일명/요약 검색...",
    "files.rootFolder": "(최상위)",
    "files.noResults": "일치하는 파일이 없습니다.",

    "relationships.help": "▶ 를 클릭해 하위 트리를 접거나 펼치세요. 수정하고 싶은 파일 이름을 클릭하면 편집 화면이 열립니다 -- 변경 사항은 즉시 aif.json에 저장됩니다.",

    "fileDetail.none": "(없음)",
    "fileDetail.dependents": "Dependents (이 파일에 의존하는 파일)",
    "fileDetail.blastRadius": "Blast radius (이 파일 변경 시 영향받는 전체 범위)",
    "fileDetail.noContent": "(내용 없음)",
    "fileDetail.copyAll": "📋 전체 복사",
    "fileDetail.includeTextRefs": "텍스트 언급(파일명만 일치)도 포함",

    "search.patternPlaceholder": "정규식 패턴 (예: TODO|FIXME)",
    "search.searching": "검색 중...",
    "search.noResults": "검색 결과 없음",
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
    "graph.editor.unlink": "Unlink",
    "graph.editor.noDependencies": "(this file has no dependencies)",
    "graph.editor.dependentsOf": (v) => `${v.n} file(s) depend on this: ${v.list}`,
    "graph.editor.linkSearchPlaceholder": "🔍 Search files to link...",
    "graph.editor.addLink": "+ Add link",

    "pack.status.running": "Running...",
    "pack.status.reviewing": "Awaiting review",
    "pack.status.error": "Error",
    "pack.status.done": "Done",
    "pack.stopSave": "Save & Stop",
    "pack.stopDiscard": "Just Stop",
    "pack.confirmStopSave": "Save progress so far and stop? Packing the same project again later will resume from here.",
    "pack.confirmStopDiscard": "Stop without saving? All progress so far will be lost.",
    "pack.guard.runningModalMessage": "Packing is still in progress. Leaving now will stop it.",
    "pack.guard.stay": "Stay",
    "pack.guard.confirm": "Confirm",
    "pack.guard.cancel": "Cancel",
    "pack.title": "Pack Progress",
    "pack.log": "Log",
    "pack.saved": (v) => `Saved: ${v.path}`,
    "pack.openResult": "Open Result",
    "pack.unknownError": "Unknown error",
    "pack.retry": "🔄 Retry",
    "pack.backToPackForm": "Back to Pack Form",
    "pack.review.moreSignatures": (v) => `+ ${v.n} more`,
    "pack.review.deleteRule": "Delete",
    "pack.review.newRulePlaceholder": "Add a new rule",
    "pack.review.addRule": "Add",
    "pack.review.backToTree": "← Back to tree",
    "pack.review.noNeedsReview": "No low-confidence summaries need review.",
    "pack.review.submit": "Finish & Save",
    "pack.review.cancel": "Cancel",
    "pack.review.confirmCancel": "Cancel and discard this review? Any unsaved edits will be lost.",
    "pack.review.leaveWithoutSaving": "Leave without saving",
    "pack.review.stayCancel": "Cancel (stay)",
    "pack.review.projectName": "Project name",
    "pack.review.aiGuide": "AI guide",
    "pack.review.codingRules": "Coding rules",
    "pack.review.folderSummariesHeader": "Folder summaries",
    "pack.review.fileRelations": "File relationships",
    "pack.review.relationsHelp": "This is the full dependency tree (click ▶ to expand/collapse a subtree). Click a file's name to open its relationship editor -- from there, click another file node in the graph to jump to it, \"Unlink\" to remove a dependency, or type a filename in the search box to add a new one. External packages (📦) are read-only.",
    "pack.review.needsReviewHeader": (v) => `⚠️ Needs review (${v.n})`,
    "pack.review.autoKeptHeader": (v) => `Auto-kept (${v.n}, still editable)`,

    "nav.pack": "📦 Pack Project",
    "nav.check": "📂 Check Project",
    "nav.options": "⚙️ Options",
    "nav.changeProject": "📂 Change Project",
    "nav.overview": "📊 Overview",
    "nav.files": "📄 Files",
    "nav.relationships": "🔗 Relationships",
    "nav.search": "🔍 Search",

    "home.tagline": "Summarizes a local project into compressed context, for an AI to read instead of the original.",

    "pack.form.projectPathPlaceholder": "e.g. C:\\path\\to\\my-project",
    "pack.form.outputPathPlaceholder": "Optional. Defaults to result/<project-name>.json",
    "pack.form.loadFiles": "Load File List",
    "pack.form.start": "Start Packing",
    "pack.form.noSafeFiles": "No selectable safe files.",
    "pack.form.allFiles": (v) => `All ${v.n} files`,
    "pack.form.dangerousDetected": (v) => `⚠️ ${v.n} sensitive file(s) detected (excluded by default -- review below and include if needed)`,
    "pack.form.dangerousExplain": "A checked file is included in the packed output (aif.json) with its original content, unredacted. Review the reason and matched line below for each one, and only include it after confirming it isn't actually sensitive.",
    "pack.form.dangerousSelectAll": "⚠️ Include all detected files",
    "pack.form.dangerousDefaultReason": "Suspected sensitive data",
    "pack.form.dangerousLine": (v) => `Line ${v.line}: ${v.text}`,
    "pack.form.noFilesSelected": "No files selected",
    "pack.form.description": "Select files to generate LLM summaries, then review/edit before saving (same as the CLI's interactive pack).",
    "pack.form.projectPathLabel": "Project folder path",
    "pack.form.outputPathLabel": "Output path (optional)",
    "pack.form.noCacheLabel": "Ignore previous pack cache (re-summarize every file, even unchanged ones)",
    "pack.form.noLlmLabel": "Don't use an LLM (no GEMINI_API_KEY needed -- summaries auto-generated from signatures/dependencies only, coding rules/AI guide skipped)",
    "pack.form.langLabel": "Packing language",
    "pack.form.langHint": "The language each file's summary/coding rules/AI guide will be written in (separate from this GUI screen's own language). English is the default and recommended choice.",
    "pack.form.langEnglish": "English (recommended)",
    "pack.form.langKorean": "Korean (한국어)",

    "check.form.aifPlaceholder": "e.g. result/my-project.json",
    "check.form.projectPlaceholder": "e.g. C:\\path\\to\\my-project (optional, for the freshness check)",
    "check.form.description": "Browse an already-packed project and copy what you need into a separate AI chat.",
    "check.form.aifLabel": "aif.json path",
    "check.form.projectLabel": "Project folder path (optional)",
    "check.form.open": "Open",
    "check.freshness.stale": (v) => `⚠️ ${v.n} changed`,
    "check.freshness.fresh": "✅ Up to date",
    "check.recentTitle": "Recent Projects",

    "options.languageTitle": "Display Language",
    "options.outputDirPlaceholder": "Defaults to result/<project-name>.json (inside Ziplex's own install folder)",
    "options.save": "Save",
    "options.saved": "Saved",
    "options.apiKeyTitle": "Gemini API Key",
    "options.apiKeyDescription": "The Gemini API key used to generate summaries/coding rules/AI guide when packing. Leave blank to use .env's GEMINI_API_KEY as-is (setting one here takes priority over it).",
    "options.apiKeyPlaceholder": "Leave blank to use .env's GEMINI_API_KEY",
    "options.geminiModelLabel": "Model",
    "options.geminiModelPlaceholder": "Leave blank for gemini-flash-latest (a pinned version like gemini-3.5-flash is recommended if it's overloaded)",
    "options.outputDirTitle": "Default Output Folder",
    "options.outputDirDescription": "The folder new packs save to by default. A project with an explicit path typed into the pack screen's \"Output path\" keeps remembering that path instead of this setting.",

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
    "options.openaiModelLabel": "Model",
    "options.openaiModelPlaceholder": "e.g. gemma2, llama3.1, gpt-4o-mini",
    "options.claudeDescription": "Your Anthropic Claude API key and model.",
    "options.claudeApiKeyPlaceholder": "Claude API key",
    "options.claudeModelLabel": "Model",
    "options.claudeModelPlaceholder": "Leave blank for claude-sonnet-4-5",

    "overview.untitled": "(untitled)",
    "overview.fileCount": (v) => `${v.n} files`,
    "overview.techStackHeading": "Tech Stack",
    "overview.techStackDeps": (v) => `(${v.n})`,

    "files.searchPlaceholder": "Search filename/summary...",
    "files.rootFolder": "(root)",
    "files.noResults": "No matching files.",

    "relationships.help": "Click ▶ to expand/collapse a subtree. Click a file's name you want to edit to open its editor -- changes save to aif.json immediately.",

    "fileDetail.none": "(none)",
    "fileDetail.dependents": "Dependents (files that depend on this one)",
    "fileDetail.blastRadius": "Blast radius (everything affected if this file changes)",
    "fileDetail.noContent": "(no content)",
    "fileDetail.copyAll": "📋 Copy All",
    "fileDetail.includeTextRefs": "Include text-only mentions (filename match, not a real import)",

    "search.patternPlaceholder": "Regex pattern (e.g. TODO|FIXME)",
    "search.searching": "Searching...",
    "search.noResults": "No results",
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
// whenever the language switcher changes (see pages/options.js's
// renderOptions()).
export function applyStaticI18n() {
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.dataset.i18n);
  }
}
