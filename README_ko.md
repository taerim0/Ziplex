# Ziplex

[English](README.md) | **한국어**

**로컬 프로젝트를 AI가 곧바로 읽을 수 있는 컨텍스트 파일 하나로 압축합니다.** 수백 개 파일을 일일이 넘겨줄 필요가 없습니다.

Ziplex는 프로젝트 전체를 훑으면서 Tree-sitter로 압축·구조화하고, LLM으로 요약을 붙인 다음(선택 사항 — API 키 없이 쓰는 방법은 [빠른 시작](#빠른-시작) 참고), 사람이 한 번 검토하고 나서야 결과물을 내보냅니다. 그 결과물이 `aif.json`입니다 — 작고 구조화된 "AI 컨텍스트 포맷" 파일입니다.

> ⚠️ 활발히 개발 중입니다. 인터페이스와 출력 포맷은 아직 바뀔 수 있습니다.

---

## 이럴 때 도움이 됩니다

- **AI 어시스턴트에게 프로젝트 구조를 매번 다시 설명하지 않아도 됨** — `aif.json`을 채팅에 붙여넣거나, MCP 클라이언트로 열거나, [GUI](#gui)에서 열면 됩니다. 세션마다 파일 구조와 컨벤션을 처음부터 다시 설명할 필요가 없습니다.
- **여러 언어가 섞인 프로젝트** — TypeScript 프론트엔드, Python/Java 백엔드, 그 사이의 설정 파일과 문서까지 — 한 번 패킹으로 파일 간 관계까지 그대로 담깁니다.
- **git 저장소가 아닌 게임 모드/에셋 프로젝트** — Godot 씬이 경로로 GDScript/Lua 스크립트를 참조하거나, Lua 모드가 느슨한 에셋 파일들로 이루어진 경우에도, Tree-sitter 문법이 없는 파일끼리의 관계까지 잡아냅니다 (자세한 내용은 [주요 기능](#주요-기능) 참고).
- **직접 작성하지 않은 코드베이스를 AI에게 맡길 때** — 요약은 저장되기 전에 사람이 검토하므로, 낯선 코드에 대한 LLM의 날것의 추측을 그대로 믿을 필요가 없습니다.
- **CI에서 컨텍스트 예산을 지켜야 할 때** — `--max-tokens`로 패킹 결과가 목표 모델이 감당할 수 있는 토큰 수를 넘으면 빌드를 실패시킵니다.
- **팀이 하나의 AI 컨텍스트를 공유할 때** — `aif.json`/`detail.json`은 그냥 파일이라, 다른 생성 산출물처럼 커밋하면 됩니다 (아래 [팀에서 사용하기](#팀에서-사용하기) 참고).

## 빠른 시작

```bash
pip install ziplex        # 또는 클론한 저장소에서: venv\Scripts\activate && pip install -e .
ziplex-gui
```

네이티브 창이 뜹니다 — 폴더 선택창으로 프로젝트 폴더를 고르고, 어떤 파일을 포함할지 체크박스로 고른 뒤(그냥 기본 안전 목록을 그대로 써도 됨), 패킹하면 끝입니다. 플래그를 외울 필요가 없습니다. API 키를 쓰기 전에 먼저 써보고 싶다면 "LLM 사용 안 함"을 체크하세요 — 가입도 네트워크 호출도 없이, 구조 정보만으로 진짜 `aif.json`이 나옵니다. 체크를 풀면(`.env`에 `GEMINI_API_KEY`를 넣거나 Options 페이지에서 다른 프로바이더를 고르면) 진짜 AI가 쓴 요약, 추론된 코딩 룰, 프로젝트 가이드를 받을 수 있습니다. 패킹은 저장 전에 검토 단계에서 멈추고, 이미 패킹한 프로젝트를 둘러보는 것도 같은 창에서 됩니다 — 전체 흐름은 [GUI](#gui) 참고.

터미널이 더 편하신가요 — 스크립트, CI, GUI 없는 환경이라면?

<details>
<summary>CLI로 빠르게 시작하기</summary>

**API 키도, 가입도, 네트워크 호출도 없이 지금 바로 써볼 수 있습니다:**

```bash
ziplex pack ./your-project/ --auto --no-llm
```

전체 파이프라인(Tree-sitter 압축, 의존성 그래프, 기술 스택 감지)이 그대로 돌아가고 진짜 `aif.json`이 나옵니다 — 요약만 LLM이 쓴 설명 대신 각 파일의 시그니처를 그대로 나열한 구조적인 문장일 뿐입니다. AI가 쓴 버전이 API 키를 쓸 만한 가치가 있는지, 자기 프로젝트에 직접 돌려보고 먼저 판단해보세요.

마음에 드셨다면 `.env`에 `GEMINI_API_KEY=...`를 추가하고(`gemini-flash-latest`가 불안정할 때는 `GEMINI_MODEL=...`도 선택적으로 추가 -- [기술 스택](#기술-스택) 참고) `--no-llm`을 빼면, 진짜 AI가 쓴 요약과 추론된 코딩 룰, 프로젝트 가이드까지 받을 수 있습니다:

```bash
ziplex pack ./your-project/                        # 전체 파이프라인, 대화형
ziplex pack ./your-project/ --auto --auto-correct  # 완전 비대화형 (CI, 스크립트용)
```

`--auto`(대화형 파일 선택 생략)와 `--auto-correct`(대화형 보정 생략)는 서로 독립된 옵션이라 마음대로 조합해서 써도 됩니다. 전에 한 번 pack한 프로젝트를 다시 pack하면, 실제로 내용이 바뀐 파일(콘텐츠 해시 기준)만 다시 요약합니다 — 나머지는 이전 요약을 그대로 재사용해서 LLM을 다시 호출하지 않습니다.

</details>

<details>
<summary><code>pack</code>의 모든 플래그</summary>

| 플래그 | 효과 |
|---|---|
| `--auto` | 대화형 파일 선택 생략, 안전한 파일 전체 포함 |
| `--auto-correct` | 대화형 보정 생략, LLM 결과를 그대로 사용 |
| `-o, --output <path>` | 출력 경로 지정 (`<path>` + `.detail.json`/`.cache.json`이 함께 저장됨) |
| `--no-cache` | 이전 pack 결과 무시하고 모든 파일 요약을 강제로 다시 생성 |
| `--include <patterns>` | `.ziplex.json`(아래)에 더해, 쉼표로 구분한 glob 패턴에 맞는 파일만 포함 |
| `--ignore <patterns>` | `.ziplex.json`에 더해, 쉼표로 구분한 glob 패턴을 추가로 제외 |
| `--max-tokens N` (+ `--max-tokens-model M`) | CI 가드: 패킹 결과가 모델 `M`(기본 GPT-4o) 기준 `N` 토큰을 넘으면 종료 코드 1 |
| `--no-llm` | `GEMINI_API_KEY`/네트워크 전혀 불필요 — 구조 정보만으로 요약, `rules`/AI 가이드 생략 |
| `--lang en\|ko` | 각 파일 summary/coding rules/AI 가이드가 *작성될* 언어(기본값이자 권장값 `en`) — 실제 코드나 주석의 언어와는 별개 |

</details>

<details>
<summary>다른 AI 프로바이더 (OpenAI 호환, Ollama, LM Studio, Claude)</summary>

기본값은 Gemini지만, LLM을 호출하는 모든 단계(요약, 코딩 룰, 프로젝트 가이드)는 다른 프로바이더로도 돌릴 수 있습니다 — `LLM_PROVIDER`(환경변수, CLI/CI용)를 설정하거나 GUI [Options 페이지](#gui)에서 고르면 됩니다(재시작 없이 바로 다음 pack부터 적용됩니다):

| 프로바이더 | `LLM_PROVIDER` | 설정 |
|---|---|---|
| Gemini (기본값) | `gemini` | `GEMINI_API_KEY`, 선택적으로 `GEMINI_MODEL` |
| OpenAI 호환 — 실제 OpenAI, Ollama, LM Studio, vLLM, OpenRouter, Groq, llama.cpp 서버 등 | `openai` | `OPENAI_API_KEY`(로컬 서버는 대부분 불필요), `OPENAI_BASE_URL`, `OPENAI_MODEL` |
| Claude (Anthropic) | `claude` | `ANTHROPIC_API_KEY`(또는 `CLAUDE_API_KEY`), 선택적으로 `CLAUDE_MODEL` |

로컬 모델(Gemma, Llama, Mistral 등)을 Ollama나 LM Studio로 서빙한다면 그냥 `openai` 프로바이더를 그 서버로 향하게 하면 됩니다 — `OPENAI_BASE_URL=http://localhost:11434/v1`(Ollama) 또는 `http://localhost:1234/v1`(LM Studio), `OPENAI_MODEL`은 해당 서버가 인식하는 이름으로. 둘 다 API 키는 필요 없습니다. GUI의 Options 페이지에는 두 서버 모두 한 번 클릭으로 채워주는 프리셋 버튼이 있습니다.

</details>

### 설정 파일

`ziplex init ./your-project/`를 실행하면 대상 프로젝트(Ziplex 저장소 자체가 아니라) 안에 `.ziplex.json`이 생겨서, `pack`을 돌릴 때마다 `include`/`ignore` 패턴을 다시 타이핑할 필요가 없습니다:

```jsonc
// your-project/.ziplex.json
{
  "include": ["src/**/*.py", "*.md"],  // 비어있으면 제외되지 않은 전부 (기본값)
  "ignore": ["**/*.generated.*"]        // DEFAULT_IGNORE/.gitignore 외에 추가로 제외할 패턴
}
```

`--include`/`--ignore` CLI 플래그는 이 파일의 패턴을 대체하는 게 아니라 더해집니다. `pack`이 뭘 수집할지 미리 보여주는 서브커맨드들(`collect`, `tree`, `tokens`, `search`, `freshness`)도 전부 같은 파일을 읽어서, 실제 `pack`이 보는 것과 어긋나지 않습니다. `aif.json`/`detail.json`처럼(아래 팀에서 사용하기 참고) 프로젝트와 함께 커밋해둘 만합니다 — 이 프로젝트를 어떻게 패킹하는지 그 자체로 문서가 됩니다.

<details>
<summary>전체 명령어</summary>

| 명령어 | 설명 |
|---|---|
| `pack <path>` | 전체 파이프라인 — `ziplex-gui`는 플래그 없이 똑같은 걸 해줍니다 |
| `init <path>` | 대상 프로젝트에 `.ziplex.json`(`include`/`ignore` glob 패턴) 생성 |
| `collect <path>` | 파일 수집 + 보안 스캔만 |
| `tokens <path>` | 압축 전/후 토큰 수 |
| `tree <path>` | 의존성 트리만 |
| `search <path> <pattern>` | 안전한 파일 전체에서 정규식 검색 (`--context N`, `--ignore-case`) |
| `detail <name>.detail.json <file-key>` | 파일 하나의 압축 본문을 부분만 읽기 (`--start`/`--end`) |
| `freshness <path> <name>.cache.json` | `aif.json`이 디스크의 실제 파일과 맞는지 해시로 확인 — LLM 호출 없음 |
| `skill <name>.json` | Claude Agent Skill로 내보내기 (`.claude/skills/<slug>/`) — MCP 서버 불필요 |
| `link <name>.json <file> <target>` / `unlink ...` | 이미 저장된 `aif.json`에 의존 관계 엣지를 추가/삭제 — `pack`을 다시 돌릴 필요 없음 |
| `settings` / `settings set <key> <value>` | `~/.ziplex/settings.json` 확인/변경 — 출력 폴더, LLM 프로바이더/API 키/모델. GUI Options 페이지의 CLI 버전 |
| `checkpoint` / `checkpoint clean [<path>\|--all]` | 중단된 `pack`이 남긴 체크포인트 파일 목록 확인/삭제 |
| `doctor [<path>]` | 환경 점검 — Python 버전, 활성 LLM 프로바이더/API 키, secretlint, 남은 체크포인트. LLM 호출 없음 |
| `signatures \| dependencies \| api \| compress \| debug <file>` | 파일 하나에 대해 추출 단계 하나만 실행 |

`ziplex --version`(`ziplex-gui --version`/`ziplex-mcp --version`도 동일)으로 설치된 버전을 확인할 수 있습니다.

</details>

## 동작 방식

Ziplex는 프로젝트 파일을 수집하고(빌드 산출물과 `.gitignore`에 걸리는 건 건너뜀), 민감 정보를 보안 스캔한 뒤, 안전한 파일만 Tree-sitter로 파싱해서 시그니처·import·API 라우트를 뽑아냅니다. 함수 본문은 마커 하나로 압축되고, 이어서 LLM이 파일별 한 줄 요약과 프로젝트 전체 코딩 룰, AI용 가이드를 작성합니다. 이 모든 걸 사람이 검토·수정할 수 있는데 — 신뢰도가 낮은 요약만 검토 대상으로 표시되고 전체를 다 보진 않아도 됩니다 — 그런 다음 가벼운 `aif.json`(바로 로드용)과 무거운 `detail.json`(필요할 때만 불러오는 압축 본문)이 저장됩니다.

## 출력 포맷

pack 한 번에 파일 세 개가 나오고, 각각 읽히는 방식이 다릅니다:

| 파일 | 담고 있는 내용 | 언제 읽히나 |
|---|---|---|
| `aif.json` | 프로젝트 가이드, 코딩 룰, 토큰 통계, 파일별 요약 + 신뢰도 점수, 전체 의존성 그래프 | 매번 — 처음부터 통째로 로드해도 될 만큼 작음 |
| `<name>.detail.json` | 파일별 압축된 원본 소스 | 요약만으로 부족할 때 필요한 파일만 (`get_detail`, GUI의 detail 뷰) |
| `<name>.cache.json` | 패킹된 모든 파일의 콘텐츠 해시 | 사람이 직접 볼 일 없음 — `check_freshness`와 incremental re-pack용 내부 관리 데이터 |

```jsonc
// aif.json — 작고 가벼워서 바로 로드되는 파일
{
  "project": {
    "name": "...", "prompt": "...",
    "tech_stack": [{ "manifest": "package.json", "language": "JavaScript/TypeScript", "package_manager": "npm", "dependencies": ["react", "..."], "dependencies_truncated": false }],
    "security_scan": { "flagged": 0, "included_anyway": 0, "excluded": 0 },
    "format_notes": "..."  // confidence/⋮---- 등을 다른 맥락 없이 이 파일만 보는 사람에게 설명해주는 고정 문구
  },
  "rules": ["..."],
  "folders": { "src": { "summary": "..." } },  // 각 폴더의 역할을 설명하는 한 문장
  "tokens": { "GPT-4o": { "original": 3100, "compressed": 749, "saved_pct": 75.8 } },
  "files": { "src/App.tsx": { "summary": "...", "confidence": 0.83 } },
  "relationships": { "src/App.tsx": { "internal": ["..."], "external": ["react"] } }
}
```

```jsonc
// out.detail.json — 더 무겁고, 파일을 자세히 들여다봐야 할 때만 가져옵니다
{
  "src/App.tsx": { "compressed": "import React ...\n    ⋮----\nexport default App" }
}
```

```jsonc
// out.cache.json — AI가 읽는 파일이 아니라 내부 관리용입니다. check_freshness가
// 나중에 비교할 수 있도록 패킹 시점 파일 내용의 해시를 저장해둡니다
{
  "src/App.tsx": "3b1c2e...(sha256)"
}
```

## 주요 기능

- **다국어 구조 인식 압축** — Python, Java, TypeScript, JavaScript, Lua, GDScript, Go, C++, Rust, C#, PHP, Ruby, Bash를 Tree-sitter로, JSON/YAML/Markdown/일반 텍스트/Dockerfile은 전용 압축기로 처리합니다. 구조는 남기고 토큰만 줄입니다. git 저장소가 아니어도, 여러 확장자 파일이 서로 얽혀 있는 로컬 파일 모음이면 뭐든 동작합니다 — 게임 모드, 에셋 프로젝트 포함.
- **내장 보안 스캔** — 모든 파일이 파이프라인에 들어오기 전에 `secretlint`(정규식 폴백)로 민감 정보를 검사합니다.
- **검토는 선택 사항, 규모에 안 밀림** — LLM 결과물(요약, 룰, 가이드, 의존성 트리)은 저장 전에 검토·수정할 수 있고, `--auto-correct`로 통째로 건너뛸 수도 있습니다. 신뢰도 낮은 요약만 검토 대상으로 표시되니, 프로젝트가 커져도 검토 시간이 그만큼 늘진 않습니다.
- **결과물을 쓰는 세 가지 방법** — Claude Code/Cursor 등을 위한 [MCP 서버](#mcp-서버), 터미널 없이 패킹/탐색하는 로컬 [GUI](#gui), 서버 없이도 되는 [Claude Agent Skill 내보내기](#claude-agent-skill-내보내기).
- **최신 상태 유지가 저렴함** — Incremental re-pack은 실제로 바뀐 파일만 다시 요약하고, LLM이 불안정하면 백오프 후 재시도하며, 실행이 실패해도 체크포인트로 이어서 진행합니다.
- **원하는 LLM을 골라 쓸 수 있음** — 기본은 Gemini지만, `LLM_PROVIDER`나 GUI Options 페이지로 OpenAI 호환 엔드포인트(실제 OpenAI, Ollama, LM Studio, vLLM, OpenRouter 등)나 Claude로 바로 바꿀 수 있습니다 — API 키도 네트워크도 필요 없는 완전 로컬 구성까지 포함해서요(자세한 내용은 [다른 AI 프로바이더](#빠른-시작) 참고).
- **원하면 API 키 없이도** — `pack --no-llm`은 LLM 호출 없이도 구조 요약, 의존성 그래프, 기술 스택 감지를 만들어냅니다. `.ziplex.json`/`--include`/`--ignore`로 범위를 좁히고, `--max-tokens`로 CI 예산을 가드할 수 있습니다.

## 테스트

```bash
pip install -e ".[dev]"   # 기본 설치에 pytest만 추가됨
pytest
```

압축기, Tree-sitter 추출기, collector의 ignore/바이너리 필터링, 의존성 그래프 연산(`build_tree`/`has_cycle`/`move_file`), 순수 `aif` 편집 API까지 — 네트워크나 `GEMINI_API_KEY` 없이 결정적으로 동작하는 핵심 로직을 커버합니다. 여기에 더해 Gemini 대신 네트워크 없는 `MockProvider`로 `pack()` 전체를 실제로 한 번 돌려서, 체크포인트·병렬 요약·토큰 계산까지 실제 LLM 호출의 비용·대기시간 없이 검증합니다.

실제 프로젝트로 `pack`을 빠르게 스모크테스트하고 싶다면: `LLM_PROVIDER=mock ziplex pack <project> --auto --auto-correct`로 1초 안에 네트워크 없이 전체 파이프라인을 돌릴 수 있습니다.

## MCP 서버

Claude Code, Cursor 등 MCP 클라이언트에서 이미 패킹된 프로젝트를 바로 질의할 수 있습니다 — `aif.json`을 프롬프트에 복사-붙여넣기할 필요 없이요.

```bash
ziplex-mcp                                                     # 직접 실행 (stdio 트랜스포트)
ziplex-mcp --aif result/Ziplex.json --project .                # 기본값을 지정해서 호출마다 안 넘겨도 되게
claude mcp add ziplex -- ziplex-mcp --aif result/Ziplex.json --project .   # Claude Code에 등록
```

`--aif`/`--project`는 이 서버의 *기본* `aif_path`/`project_path`를 지정합니다 — 아래 모든 툴은 여전히 명시적으로 넘기는 값을 우선하지만, 세션 내내 하나의 패킹된 프로젝트만 다룬다면 매 호출마다 같은 경로를 반복 안 넘겨도 됩니다. 두 플래그 다 생략해도 무방합니다 — 그러면 예전처럼 매 호출마다 경로가 필요할 뿐입니다.

| 툴 | 하는 일 |
|---|---|
| `get_overview(aif_path?, project_path?)` | 프로젝트 가이드, 코딩 룰, 토큰 통계 — 가장 먼저 호출하면 됩니다 |
| `list_files(aif_path?, project_path?, folder?, confidence_below?)` | 모든 파일을 요약 + 신뢰도 점수와 함께 매핑 — 폴더 하나로 좁히거나 신뢰도 기준치로 필터링 가능 |
| `get_folders(aif_path?)` | 모든 폴더를 그 역할을 설명하는 한 문장과 함께 매핑 |
| `get_relationships(aif_path?, files?)` | 전체 의존성 그래프를 한 번에 — 모든 파일의 내부/외부 엣지 — 또는 지정한 파일들만 |
| `get_dependents(aif_path?, file)` | `file`을 직접 의존하는 파일들 |
| `get_blast_radius(aif_path?, file)` | `file`이 바뀌면 직간접적으로 영향받는 모든 파일 |
| `get_detail(aif_path?, file, start_line?, end_line?)` | 파일의 압축 소스, 전체 또는 줄 범위로 |
| `check_freshness(project_path?, aif_path?)` | 패킹 결과가 디스크의 실제 파일과 맞는지 해시로 확인 — LLM 호출 없음 |
| `search_project(project_path?, pattern, ...)` | 프로젝트 원본 파일 전체에서 정규식 검색 |

의도적으로 읽기 전용입니다 — 모든 툴은 사람이 `correct_aif()`로 이미 검토한 `aif.json`/`detail.json`을 그대로 서빙할 뿐, 어떤 툴도 프로젝트를 알아서 다시 패킹하거나 보정하지 않습니다. 그건 Ziplex의 핵심인 human-in-the-loop 단계를 건너뛰는 거니까요. `get_dependents`/`get_blast_radius`도 `pack`이 만드는 것과 같은, 사람이 보정한 `relationships` 그래프 위에서 동작합니다 — 방금 추출한 검토 안 된 그래프가 아니라요.

`aif.json`/`detail.json`은 마지막 `pack` 실행 시점의 스냅샷이라, 활발히 바뀌는 프로젝트에서는 실제 상태와 어긋날 수 있습니다. `search_project`(항상 파일을 직접 읽음)를 제외한 나머지 툴은 전부 이 스냅샷을 그대로 믿습니다 — 오래됐을까 걱정되면 `check_freshness`부터 호출하거나, `get_overview`/`list_files`에 `project_path`를 함께 넘겨서 알아서 확인하게 하세요: 스냅샷이 오래됐으면 결과에 `_stale` 필드가 추가로 붙어서 옵니다. 뭘 고쳐주진 않지만, 나머지를 믿기 전에 다시 `pack`할 필요가 있는지는 알려줍니다.

## GUI

터미널을 쓰지 않고 프로젝트를 패킹하거나, Claude Code(또는 MCP 자체)를 쓸 수 없지만 브라우저 기반 AI 챗은 쓸 수 있는 환경에서 이미 패킹된 프로젝트를 둘러보는 용도의 로컬 단일 사용자 GUI입니다.

```bash
ziplex-gui                                            # 네이티브 창 (pywebview)
ziplex-gui --aif out.json --project ./your-project/   # 시작 화면 미리 채우기
ziplex-gui --no-window                                # 창 대신 일반 브라우저 탭
```

**GUI에서 패킹하기** — 네이티브 폴더 선택창으로 프로젝트 폴더를 고르고, 어떤 파일을 포함할지 체크박스로 고른 뒤(`collect`의 보안 스캔이 만드는 것과 같은 safe/dangerous 구분), 패킹 언어(`pack --lang`의 GUI 버전 — summary/rules/AI 가이드가 실제로 *작성될* 언어이며, 아래 옵션 페이지의 화면 표시 언어와는 별개)를 고르고, 원하면 "LLM 사용 안 함"(`pack --no-llm`의 GUI 버전)도 체크하고, 백그라운드에서 돌아가는 패킹 과정을 지켜봅니다. 분석이 끝나면 저장 전에 검토 단계에서 멈춥니다 — 프로젝트 이름, 가이드, 룰, 파일별 요약을 고칠 수 있고(CLI와 같은 기준으로 신뢰도가 낮은 것만 검토 대상으로 표시됩니다), 의존성 그래프는 먼저 접었다 펼 수 있는 전체 트리로 보여줍니다 — 고칠 파일을 발견하면 그 파일 이름을 클릭해 엣지 하나하나를 잇거나 끊는 편집 화면으로 들어갑니다 (부모가 여러 개인 파일이라도 다른 참조는 그대로 남습니다).

**기존 pack 둘러보기** — MCP 서버가 노출하는 overview/files/relationships/detail/search 뷰를 MCP 툴 호출 대신 웹 페이지로 그대로 씁니다. Relationships 페이지는 패킹 때와 같은 "전체 트리 먼저, 파일 하나 편집은 그다음" 흐름을 그대로 써서, 패킹이 끝난 뒤에 발견한 관계 문제도 파이프라인을 다시 돌리지 않고 고칠 수 있습니다. 각 페이지엔 복사 버튼이 있고, 의도된 흐름은 여기서 둘러본 다음 필요한 내용을 별도의 AI 챗에 직접 붙여넣는 것입니다 — GUI가 그 챗과 직접 통신하지는 않습니다.

**Options 페이지** — 표시 언어, 새로 패킹할 때 기본으로 저장되는 출력 폴더, 그리고 패킹 전체에 쓸 AI 프로바이더(Gemini / OpenAI 호환 / Claude — [다른 AI 프로바이더](#빠른-시작) 참고)를 고릅니다. Ollama와 LM Studio의 기본 로컬 포트를 한 번 클릭으로 채워주는 프리셋 버튼도 있어서, 완전 로컬 구성도 URL을 직접 타이핑할 필요가 없습니다.

`127.0.0.1`에만 바인딩됩니다 — `--host` 옵션이 없고, 네트워크에 노출할 방법도 없습니다.

## Claude Agent Skill 내보내기

패킹된 프로젝트에 접근하는 세 번째 방법입니다 — MCP 서버나 GUI 대신, Claude Code는 쓸 수 있지만 MCP 서버 등록까지는 부담스러운 순간을 위한 것입니다:

```bash
ziplex skill result/my-project.json               # .claude/skills/my-project/ 생성
ziplex skill result/my-project.json -o some/dir    # 출력 디렉터리 직접 지정
```

[Claude Agent Skill](https://code.claude.com/docs/en/skills)을 만듭니다 — `SKILL.md`와 `references/overview.md`/`files.md`/`relationships.md`/`detail.json`으로 구성되며, `.claude/skills/` 아래에 두기만 하면 서버 프로세스 없이도 Claude Code가 알아서 인식하고 점진적으로 불러옵니다. [repomix](https://repomix.com/guide/agent-skills-generation)의 동일한 `--skill-generate` 기능과 다른 점: `references/files.md`엔 원본 코드를 통째로 넣지 않습니다 — `aif.json` 자체가 이미 그렇듯 요약과 신뢰도 점수까지만 담고, 압축된 본문은 `references/detail.json`으로만 나갑니다. 생성된 디렉터리를 커밋하는 것도 `aif.json`/`detail.json`을 커밋하는 것과 같은 방식입니다(아래 팀에서 사용하기 참고) — 그냥 파일일 뿐이니까요.

## 팀에서 사용하기

Ziplex는 한 사람의 로컬 스냅샷을 패킹합니다 — 공유 서버나 실시간 동기화는 없습니다. 그렇다고 팀에서 못 쓴다는 건 아닙니다: `aif.json`/`detail.json`/`cache.json`은 그냥 파일이라, 다른 자동 생성 산출물처럼 프로젝트 저장소에 커밋해서 팀이 하나의 기준선을 공유할 수 있습니다.

- 의미 있는 변경 뒤에 `pack`을 돌린 사람이 코드 변경과 함께 새로 생성된 결과물을 커밋합니다.
- 다른 사람들의 `aif.json`은 마지막으로 커밋된 시점만큼만 최신입니다 — `check_freshness`(또는 위에서 설명한, `project_path`를 넘긴 `get_overview`/`list_files`)로 자기 작업 사본이 커밋된 것과 얼마나 어긋났는지 다시 `pack`하지 않고도 확인할 수 있습니다.
- 이건 실시간 동기화가 아니라 권장 컨벤션입니다 — Ziplex가 강제하거나 조율하지 않습니다. 병합/충돌 해결도 없고, 두 사람이 각자 커밋하기 전에 따로 `pack`을 돌리면 "누구 결과가 맞는지"도 정해져 있지 않습니다. 실제로 충돌이 나면 JSON을 손으로 병합하지 마세요 — [merge-conflicts.md](docs/team/merge-conflicts.md)에 권장 해결법(충돌 해소 → 재`pack` → 검토)이 있고, [`gitattributes`](docs/team/gitattributes)를 적용해두면 GitHub PR diff에서 이 파일들이 접힌 채로 표시됩니다.

**CI 게이트 (선택):** `ziplex freshness <project> <name>.cache.json`은 커밋된 결과물이 코드와 어긋났을 때 0이 아닌 코드로 종료됩니다 — LLM 호출도, `GEMINI_API_KEY`도 필요 없이 해시 비교만 합니다. PR 체크로 연결해두면([템플릿](docs/ci/freshness-gate.yml)) 오래된 `aif.json`이 재검토 없이 병합되는 걸 막을 수 있습니다. `pack` 자체를 자동화하는 건 아닙니다 — 실제 LLM 비용이 들고 사람의 검토 단계를 건너뛰게 되므로, 의도적으로 수동 단계로 남겨뒀습니다.

## 기술 스택

Python 3.10+ · [Tree-sitter](https://tree-sitter.github.io/tree-sitter/) (Python/Java/TypeScript/JavaScript/Lua/GDScript/Go/C++/Rust/C#/PHP/Ruby/Bash 문법, GDScript는 `tree-sitter-language-pack` 경유) · [tiktoken](https://github.com/openai/tiktoken) · [ruamel.yaml](https://yaml.readthedocs.io/) · `requests`를 통한 순수 REST 호출로 Gemini(기본값 `gemini-flash-latest`, `GEMINI_MODEL`로 재정의 가능), OpenAI 호환 엔드포인트(실제 OpenAI, Ollama, LM Studio, vLLM, OpenRouter 등), 또는 Claude 중 선택 — [다른 AI 프로바이더](#빠른-시작) 참고 · [MCP](https://modelcontextprotocol.io/) · Flask · pywebview · `secretlint` · `pathspec`

## 라이선스

[MIT](LICENSE)
