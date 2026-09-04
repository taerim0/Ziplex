import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .extract.code.extractor import extract_signatures, extract_dependencies, extract_api, debug_tree
from .extract.code.compressor import compress_file
from .file.collector import collect_files, print_tree as print_file_tree
from .file.scanner import scan_files
from .file.media import classify_media_file
from .file.textutil import relative_key as _rel_key
from .text_references import find_text_references_for_file
from .go_packages import read_go_module_path, build_go_package_index, expand_go_dependencies
from .tokenizer import analyze_tokens, analyze_tokens_with_compression
from .llm import LANGUAGE_NAMES, DEFAULT_PROVIDER_NAME, PROVIDERS, GeminiProvider, OpenAIProvider, ClaudeProvider
from .packager import pack, save_aif
from .corrector import correct_aif
from .edits import finalize_aif
from .file.relationship import (
    build_tree, print_tree as print_dependency_tree, add_relationship, remove_relationship, CycleError,
)
from .search import search_files, read_detail_range
from .freshness import check_freshness_scoped, load_pack_scope
from .skill_export import export_skill
from .config import init_config, CONFIG_FILENAME, collection_kwargs as _collection_kwargs, collect_and_scan as _collect_and_scan
from . import __version__
from . import settings as app_settings
from . import checkpoint as app_checkpoint
from . import doctor as app_doctor
from .file.textutil import human_size as _human_size


def _split_patterns(value: str | None) -> list[str] | None:
    """Splits a --include/--ignore CLI value on "," into a pattern list,
    stripping whitespace around each one -- "src/**/*.py, *.md" (a space
    after the comma, a natural way to type the list) would otherwise leave
    " *.md" as a literal leading-space pattern that pathspec matches nothing
    against, silently dropping every intended file with no error. None stays
    None (no flag given), matching collect_files()'s own "no filter" default.
    """
    if not value:
        return None
    return [p.strip() for p in value.split(",") if p.strip()]


_SECRET_FIELDS = ("gemini_api_key", "openai_api_key", "claude_api_key")

# The real, user-facing provider choices for `ziplex settings set llm_provider`
# -- llm.PROVIDERS minus "mock", which exists there only for tests
# (LLM_PROVIDER=mock) and would otherwise silently become the process-wide
# default for every future pack (CLI and GUI) if accepted here, fabricating
# plausible-looking MockProvider summaries with no error. The GUI's own
# Options page provider selector excludes it the same way.
_REAL_PROVIDER_NAMES = tuple(name for name in PROVIDERS if name != "mock")

# Shown next to each unset field in `ziplex settings` -- mirrors settings.py's
# own inline comments on DEFAULT_SETTINGS (the single source of truth for
# what each fallback actually is); kept here rather than read off that
# module since these are display-only prose, not values anything resolves
# against.
# Built from llm.py's own DEFAULT_MODEL/DEFAULT_BASE_URL/DEFAULT_PROVIDER_NAME
# constants, not retyped literals -- a code-review finding: a hardcoded copy
# here could silently drift the moment one of those defaults changes (e.g. a
# provider's default model gets bumped), leaving `ziplex settings` printing a
# stale fallback with nothing tying the two together.
_SETTINGS_FIELD_HINTS = {
    "output_dir": "미설정 -- 프로젝트별 기본 출력 폴더(result/) 사용",
    "gemini_api_key": "미설정 -- GEMINI_API_KEY 환경변수(.env) 사용",
    "gemini_model": f"미설정 -- GEMINI_MODEL 환경변수 또는 기본 모델({GeminiProvider.DEFAULT_MODEL}) 사용",
    "llm_provider": f"미설정 -- LLM_PROVIDER 환경변수 또는 기본값({DEFAULT_PROVIDER_NAME}) 사용",
    "openai_api_key": "미설정 -- OPENAI_API_KEY 환경변수 사용",
    "openai_base_url": f"미설정 -- 기본값({OpenAIProvider.DEFAULT_BASE_URL}) 사용",
    "openai_model": f"미설정 -- 기본 모델({OpenAIProvider.DEFAULT_MODEL}) 사용",
    "claude_api_key": "미설정 -- ANTHROPIC_API_KEY/CLAUDE_API_KEY 환경변수 사용",
    "claude_model": f"미설정 -- 기본 모델({ClaudeProvider.DEFAULT_MODEL}) 사용",
}


def _mask_secret(value: str) -> str:
    """Never prints a stored API key in full -- only its last 4 characters,
    same "something to recognize it by, nothing to steal" tradeoff a
    password manager's own masked display makes. A short value (<=4 chars,
    never a real key but cheap to guard anyway) masks in full rather than
    echoing itself back unmasked.
    """
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _print_settings(settings: dict) -> None:
    """`ziplex settings`'s read path -- settings.EDITABLE_FIELDS in
    declaration order, each either its real value (masked for
    _SECRET_FIELDS) or _SETTINGS_FIELD_HINTS' explanation of what applies
    instead. `project_output_dirs` is summarized as a count only (not
    editable via this command -- see settings.EDITABLE_FIELDS' own
    docstring for why), not printed key-by-key, since a long-lived install
    could have many pins and this command is about the provider/model
    settings, not an audit of every per-project folder pin.
    """
    print(f"⚙️  Ziplex 설정 ({app_settings.SETTINGS_PATH})\n")
    for field in app_settings.EDITABLE_FIELDS:
        value = settings.get(field) or ""
        if not value:
            # .get() with a generic fallback, not a bare index -- a field
            # added to settings.EDITABLE_FIELDS without a matching entry
            # here used to crash `ziplex settings` with an uncaught
            # KeyError, breaking the "never raise" contract settings.py/
            # config.py otherwise hold. _SETTINGS_FIELD_HINTS should still
            # be kept in sync for a real explanation -- this is a safety
            # net, not a reason to stop updating it.
            print(f"  {field}: ({_SETTINGS_FIELD_HINTS.get(field, '미설정')})")
        elif field in _SECRET_FIELDS:
            print(f"  {field}: {_mask_secret(value)} (설정됨)")
        else:
            print(f"  {field}: {value}")
    pin_count = len(settings.get("project_output_dirs") or {})
    print(f"\nproject_output_dirs: {pin_count}개 프로젝트에 폴더 핀 고정됨 (GUI Options 페이지에서 확인)")


def _print_checkpoints(checkpoints: list[dict]) -> None:
    """`ziplex checkpoint list`'s read path. Each entry's own recorded
    project name (not the checkpoint filename's one-way hash suffix -- see
    checkpoint.list_checkpoints()'s docstring for why that can't be
    reversed back into a project path) plus how many files it has pending
    and how long ago it was saved, so a human can tell a fresh in-flight
    checkpoint apart from one abandoned months ago worth clearing.
    """
    if not checkpoints:
        print(f"체크포인트 없음 ({app_checkpoint.CHECKPOINT_DIR})")
        return
    print(f"📂 체크포인트 {len(checkpoints)}개 ({app_checkpoint.CHECKPOINT_DIR})\n")
    for cp in checkpoints:
        saved_at = datetime.fromtimestamp(cp["modified"]).strftime("%Y-%m-%d %H:%M")
        print(f"  {cp['path'].name}")
        print(f"    프로젝트: {cp['project_name']} | 대기 중인 파일: {cp['pending_files']}개"
              f" | {_human_size(cp['size_bytes'])} | 저장 시각: {saved_at}")


def _print_doctor(report: dict) -> None:
    """`ziplex doctor`'s read path -- one line per check, ✅/⚠️/❌ marking
    whether it needs attention (❌ blocks a real pack from working at all;
    ⚠️ degrades gracefully to an already-working fallback; ℹ️ is purely
    informational, neither state is wrong).
    """
    print("🩺 Ziplex 환경 점검\n")
    print(f"  Ziplex 버전: {report['ziplex_version']}")

    py_ok = "✅" if report["python_ok"] else "❌"
    print(f"  {py_ok} Python 버전: {report['python_version']} (요구: >= {report['python_min']})")

    print(f"  활성 LLM Provider: {report['llm_provider']} (모델: {report['llm_model']})")
    if report["llm_api_key_present"]:
        print("  ✅ API Key: 설정됨")
    else:
        print("  ❌ API Key: 없음 -- pack 시 LLM 요약 실패 -- `ziplex settings set` 또는 .env로 설정하거나 `pack --no-llm` 사용")

    if report["secretlint_available"]:
        print("  ✅ secretlint: 사용 가능")
    else:
        print("  ⚠️  secretlint: 없음 -- 정규식 기반 보안 스캔으로 대체됨 (Windows npm 전역 설치의 알려진 제약일 수 있음)")

    settings_state = "있음" if report["settings_file_present"] else "없음 (기본값 사용 중)"
    print(f"  ℹ️  {app_settings.SETTINGS_PATH}: {settings_state}")

    if report["checkpoint_count"]:
        print(f"  ⚠️  남은 체크포인트: {report['checkpoint_count']}개 -- `ziplex checkpoint`로 확인")
    else:
        print("  ✅ 남은 체크포인트: 없음")

    if "project_path" in report:
        print()
        proj_ok = "✅" if report["project_is_dir"] else "❌"
        print(f"  {proj_ok} 프로젝트 경로: {report['project_path']}")
        if report["project_is_dir"]:
            env_state = "있음" if report["project_has_env_file"] else "없음"
            git_state = "예" if report["project_is_git_repo"] else "아니오"
            print(f"  ℹ️  .env 파일: {env_state}")
            print(f"  ℹ️  git 저장소: {git_state} (freshness-gate CI 사용 시 참고)")


# Every other message in this CLI is Korean by long-standing convention (see
# AGENTS.md) -- this one's the deliberate exception. A bare `ziplex` with no
# subcommand used to silently exit 0 with no output at all (the if/elif
# dispatch chain in main() just matches nothing); reported directly as
# wanting a real command list here, and in English specifically -- this is
# often the very first thing anyone lands on (following the English-first
# README, or just typing the bare command name to see what's there), before
# they've necessarily learned this CLI's own output is Korean everywhere
# else. `ziplex <command> --help`/`ziplex --help` are untouched (still
# argparse's own full, Korean help text) -- this is a friendlier, shorter
# alternative shown only on a genuinely empty invocation, not a translation
# of the whole help system.
_COMMAND_OVERVIEW = [
    ("pack <path>", "Full pipeline (or just run `ziplex-gui` -- no flags to remember)"),
    ("init <path>", "Scaffold .ziplex.json (include/ignore glob patterns)"),
    ("collect <path>", "File collection + security scan only"),
    ("tokens <path>", "Token count, before/after compression"),
    ("tree <path>", "Dependency tree only"),
    ("search <path> <pattern>", "Regex search across all safe files"),
    ("detail <name>.detail.json <file>", "Partial read of one file's compressed body"),
    ("freshness <path> <name>.cache.json", "Hash-check aif.json against disk -- no LLM calls"),
    ("skill <name>.json", "Export as a Claude Agent Skill"),
    ("link / unlink <name>.json <file> <target>", "Add/remove a dependency edge"),
    ("settings [set <key> <value>]", "View/change ~/.ziplex/settings.json"),
    ("checkpoint [clean [<path>|--all]]", "List/delete leftover checkpoint files"),
    ("doctor [<path>]", "Environment sanity check -- no LLM calls"),
    ("signatures | dependencies | api | compress | debug <file>", "One extraction step on a single file"),
]


def _print_command_overview() -> None:
    print(f"Ziplex {__version__} -- local project -> aif.json, a token-reduced context format for AI\n")
    print("Usage: ziplex <command> [options]\n")
    width = max(len(cmd) for cmd, _ in _COMMAND_OVERVIEW)
    for cmd, desc in _COMMAND_OVERVIEW:
        print(f"  {cmd.ljust(width)}  {desc}")
    print("\nRun `ziplex <command> --help` for a command's full options, or `ziplex-gui` for the GUI.")


def _edit_saved_relationship(aif_path: str, file_name: str, target: str, edit_fn, verb: str) -> None:
    """Shared body for `ziplex link`/`ziplex unlink` -- both are a one-shot
    wrapper over file/relationship.py's add_relationship()/
    remove_relationship(), operating directly on an already-packed
    aif.json's `relationships` field. The same shape gui_server.py's
    /api/relationships/link|unlink routes edit (via gui/pack_service.py's
    link_saved_relationship()/unlink_saved_relationship()) -- not reused
    directly here since that pair also carries per-path locking meant for
    a long-running server juggling concurrent requests, which a one-shot
    CLI invocation (start, do one edit, exit) has no need for. Never
    touches detail.json/cache.json -- only `relationships` changes,
    rewritten byte for byte identical apart from that one field, same
    "don't call packager.save_aif() on an already-finished pack" reasoning
    pack_service.py's own _edit_saved_relationships() documents.

    Added specifically so a wrong dependency edge can be fixed without
    pack's own interactive `correct_relationships()` loop -- built for a
    single reparent move at a time via typed numbers, which doesn't scale
    past a handful of files (see that function's own module docstring for
    the fuller reasoning; the GUI's relationship editor is still the
    better tool for anything past a couple of quick edge fixes).
    """
    try:
        with open(aif_path, "r", encoding="utf-8") as f:
            aif = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ {aif_path} 읽기 실패: {e}")
        sys.exit(1)

    relationships = aif.get("relationships")
    if relationships is None:
        print(f"❌ {aif_path}에 relationships가 없습니다 -- pack이 완료된 aif.json인지 확인하세요")
        sys.exit(1)

    try:
        edit_fn(relationships, file_name, target)
    except (ValueError, CycleError) as e:
        print(f"⚠️  {e}")
        sys.exit(1)

    with open(aif_path, "w", encoding="utf-8") as f:
        json.dump(aif, f, ensure_ascii=False, indent=2)

    print(f"✅ {file_name} → {target} {verb}")


def _check_max_tokens(aif_tokens: dict, max_tokens: int, model: str) -> tuple[bool, int | None]:
    """CI-guard check for `pack --max-tokens`: does the packed payload
    (aif.json's actual per-file token cost, tokenizer.py's "compressed"
    figure -- see analyze_tokens_with_payload) fit under a budget?

    Returns (passed, actual_count). actual_count is None (and passed is
    always False) when `model` isn't a key in aif_tokens (aif["tokens"],
    keyed by tokenizer.MODEL_ENCODINGS) -- lets the caller tell "over
    budget" apart from "typo'd --max-tokens-model" instead of one silently
    reading as the other.

    Pure and side-effect-free on purpose (no printing, no sys.exit) so it's
    directly testable -- main() owns turning the result into CLI output and
    an exit code.
    """
    model_data = aif_tokens.get(model)
    if model_data is None:
        return False, None
    actual = model_data["compressed"]
    return actual <= max_tokens, actual


def main():
    # Windows consoles default to the system locale's codepage (e.g. cp949 on
    # Korean Windows), not UTF-8 -- printing an emoji then raises
    # UnicodeEncodeError before pack() gets anywhere. Force UTF-8 on real
    # process stdout/stderr; guarded with hasattr since a piped/captured
    # stream (tests, some CI runners) may not support reconfigure() at all.
    # Lives in main() itself (not just the `if __name__ == "__main__":` guard
    # below) so the `ziplex` console-script entry point -- which imports this
    # module and calls main() directly, never executing as __main__ -- gets
    # the same fix; a bare `python cli.py` run still hits it too since main()
    # is the very next thing __main__ calls either way.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Ziplex")
    parser.add_argument("--version", action="version", version=f"ziplex {__version__}",
                         help="버전 정보 출력")
    sub = parser.add_subparsers(dest="command")

    c = sub.add_parser("compress", help="코드 압축")
    c.add_argument("file", help="파일 경로")

    s = sub.add_parser("signatures", help="시그니처 추출")
    s.add_argument("file", help="파일 경로")

    d = sub.add_parser("dependencies", help="의존성 추출")
    d.add_argument("file", help="파일 경로")

    a = sub.add_parser("api", help="API 추출")
    a.add_argument("file", help="파일 경로")

    db = sub.add_parser("debug", help="트리 구조 출력")
    db.add_argument("file", help="파일 경로")

    col = sub.add_parser("collect", help="파일 수집")
    col.add_argument("path", help="프로젝트 폴더 경로")

    tok = sub.add_parser("tokens", help="토큰 카운팅")
    tok.add_argument("path", help="프로젝트 폴더 경로")

    p = sub.add_parser("pack", help="프로젝트 패킹")
    p.add_argument("path", help="프로젝트 폴더 경로")
    p.add_argument("--output", "-o", default=None, help="출력 파일 경로 (기본값: result/<프로젝트 폴더명>.json)")
    p.add_argument("--auto", action="store_true", help="파일 자동 선택 (전체 안전 파일 포함)")
    p.add_argument("--auto-correct", action="store_true", help="LLM 결과 자동 승인 (대화형 보정 건너뜀)")
    p.add_argument("--no-cache", action="store_true", help="변경 없는 파일도 요약을 다시 생성 (이전 pack 재사용 끄기)")
    p.add_argument("--no-llm", action="store_true",
                    help="LLM 호출 없이 구조 정보(시그니처/의존성)만으로 패킹 -- GEMINI_API_KEY 불필요, rules/AI 가이드는 생성되지 않음")
    p.add_argument("--include", default=None, help="포함할 glob 패턴, 쉼표로 구분 (예: 'src/**/*.py,*.md') -- .ziplex.json의 include에 추가됨")
    p.add_argument("--ignore", default=None, help="추가로 제외할 glob 패턴, 쉼표로 구분 -- .ziplex.json의 ignore에 추가됨")
    p.add_argument("--max-tokens", type=int, default=None, metavar="N",
                    help="패킹된 aif.json의 파일별 payload가 N 토큰을 넘으면 종료 코드 1로 실패 -- CI에서 컨텍스트 예산 초과를 막는 용도")
    p.add_argument("--max-tokens-model", default="GPT-4o", metavar="MODEL",
                    help="--max-tokens 판단 기준 모델 (기본값: GPT-4o, tokenizer.MODEL_ENCODINGS의 키 중 하나)")
    p.add_argument("--lang", choices=list(LANGUAGE_NAMES), default="en",
                    help="패킹 결과(파일별 summary/rules/AI 가이드)의 언어 (기본값 및 권장값: en)")

    tr = sub.add_parser("tree", help="의존성 트리 출력")
    tr.add_argument("path", help="프로젝트 폴더 경로")

    se = sub.add_parser("search", help="프로젝트 전체 검색 (정규식)")
    se.add_argument("path", help="프로젝트 폴더 경로")
    se.add_argument("pattern", help="검색할 정규식 패턴")
    se.add_argument("--context", "-C", type=int, default=0, help="매치 앞뒤로 보여줄 줄 수")
    se.add_argument("--ignore-case", "-i", action="store_true", help="대소문자 무시")

    de = sub.add_parser("detail", help="detail.json에서 파일 일부만 읽기")
    de.add_argument("detail_path", help="<name>.detail.json 경로")
    de.add_argument("file", help="detail.json 안의 파일 키")
    de.add_argument("--start", type=int, default=None, help="시작 줄 번호 (1-based)")
    de.add_argument("--end", type=int, default=None, help="끝 줄 번호 (1-based, 포함)")

    fr = sub.add_parser("freshness", help="aif.json이 최신 상태인지 확인 (해시 비교, LLM 호출 없음)")
    fr.add_argument("path", help="프로젝트 폴더 경로")
    fr.add_argument("cache_path", help="<name>.cache.json 경로")

    sk = sub.add_parser("skill", help="aif.json을 Claude Agent Skill로 내보내기 (.claude/skills/, MCP 서버 없이도 인식됨)")
    sk.add_argument("aif_path", help="aif.json 경로")
    sk.add_argument("--output", "-o", default=None, help="출력 디렉터리 (기본값: .claude/skills/<프로젝트명>/)")

    ini = sub.add_parser("init", help="프로젝트에 .ziplex.json 설정 파일 생성 (include/ignore 패턴)")
    ini.add_argument("path", help="프로젝트 폴더 경로")

    st = sub.add_parser(
        "settings",
        help="Ziplex 전역 설정 확인/변경 (~/.ziplex/settings.json -- 기본 출력 폴더, LLM provider/API key/모델, GUI Options 페이지와 동일한 값)",
    )
    st_sub = st.add_subparsers(dest="settings_action")
    st_sub.add_parser("get", help="현재 설정 출력 (인자 없이 `ziplex settings`만 실행해도 동일)")
    st_set = st_sub.add_parser("set", help="설정값 하나를 변경")
    st_set.add_argument("key", choices=list(app_settings.EDITABLE_FIELDS), help="변경할 필드 이름")
    st_set.add_argument("value", help="설정할 값 -- 빈 문자열(\"\")을 주면 미설정 상태로 되돌림")

    ckp = sub.add_parser(
        "checkpoint",
        help="pack 중단 후 남은 체크포인트 파일 확인/삭제 (checkpoint/*.json -- 재개용 임시 파일, LLM 호출 없음)",
    )
    ckp_sub = ckp.add_subparsers(dest="checkpoint_action")
    ckp_sub.add_parser("list", help="남은 체크포인트 목록 출력 (인자 없이 `ziplex checkpoint`만 실행해도 동일)")
    ckp_clean = ckp_sub.add_parser("clean", help="체크포인트 삭제")
    ckp_clean.add_argument("path", nargs="?", default=None, help="이 프로젝트의 체크포인트만 삭제 (프로젝트 폴더 경로 -- pack에 준 경로와 동일해야 함)")
    ckp_clean.add_argument("--all", action="store_true", help="모든 프로젝트의 체크포인트를 전부 삭제")

    dc = sub.add_parser(
        "doctor",
        help="환경 점검 -- Python 버전/활성 LLM provider/API key/secretlint/남은 체크포인트를 한 번에 확인 (LLM 호출 없음)",
    )
    dc.add_argument("path", nargs="?", default=None, help="선택: 프로젝트 폴더 경로 (주면 .env/git 저장소 여부도 확인)")

    lk = sub.add_parser(
        "link",
        help="이미 저장된 aif.json에서 두 파일 사이 의존 관계를 한 번에 연결 (pack의 인터랙티브 관계 수정 루프 대신 쓰는 one-shot 명령어)",
    )
    lk.add_argument("aif_path", help="aif.json 경로")
    lk.add_argument("file", help="의존하는 쪽 파일 (relationships의 키, 예: src/a.py)")
    lk.add_argument("target", help="의존받는 쪽 파일")

    ulk = sub.add_parser("unlink", help="이미 저장된 aif.json에서 두 파일 사이 의존 관계를 해제")
    ulk.add_argument("aif_path", help="aif.json 경로")
    ulk.add_argument("file", help="의존하는 쪽 파일")
    ulk.add_argument("target", help="의존받는 쪽 파일")

    args = parser.parse_args()

    if args.command == "compress":
        print(compress_file(args.file))

    elif args.command == "signatures":
        sigs = extract_signatures(args.file)
        for sig in sigs:
            print(f"  {sig}")

    elif args.command == "dependencies":
        deps = extract_dependencies(args.file)
        for dep in deps:
            print(f"  {dep}")

    elif args.command == "api":
        apis = extract_api(args.file)
        for api in apis:
            print(f"  {api}")

    elif args.command == "debug":
        debug_tree(args.file)

    elif args.command == "collect":
        files = collect_files(args.path, **_collection_kwargs(args.path))
        scan_result = scan_files(files)

        print(f"\n📁 수집된 파일: {len(files)}개")
        print_file_tree(files, args.path)

        if scan_result["dangerous"]:
            print(f"\n⚠️  민감 파일 감지: {len(scan_result['dangerous'])}개")
            for d in scan_result["dangerous"]:
                print(f"  ❌ {d['file']} -- {d.get('reason') or '민감 정보로 추정됨'}")

        print(f"\n✅ 안전한 파일: {len(scan_result['safe'])}개")

    elif args.command == "tokens":
        safe_files = _collect_and_scan(args.path)["safe"]

        results, _ = analyze_tokens_with_compression(safe_files)
        # analyze_tokens_with_compression() silently skips any file it can't
        # read as text (matches its own before/after-*compression* scope --
        # a media asset, see file/media.py, has no text body to compress in
        # the first place). The printed count reflects only what was
        # actually measured, not every safe file, so it doesn't imply more
        # was covered than really was.
        media_count = sum(1 for f in safe_files if classify_media_file(f))
        measured_count = len(safe_files) - media_count
        note = f" (미디어 자산 {media_count}개 제외)" if media_count else ""
        print(f"\n📊 토큰 분석 ({measured_count}개 파일{note})\n")
        for model, data in results.items():
            print(f"{model}")
            print(f"  압축 전: {data['original']:,} / {data['max']:,} {data['original_bar']}")
            print(f"  압축 후: {data['compressed']:,} / {data['max']:,} {data['compressed_bar']}")
            print(f"  절감:    {data['saved']:,} 토큰 ({data['saved_pct']}% 감소)\n")

    elif args.command == "pack":
        # --auto-correct also means no terminal to prompt if an LLM call
        # keeps failing inside pack() itself (see handle_llm_failure).
        aif = pack(
            args.path, auto=args.auto, interactive=not args.auto_correct, use_cache=not args.no_cache,
            use_llm=not args.no_llm,
            include=_split_patterns(args.include),
            ignore=_split_patterns(args.ignore),
            lang=args.lang,
        )
        if aif:
            if args.auto_correct:
                aif = finalize_aif(aif)  # skip interactive review, still build relationships
            else:
                aif = correct_aif(aif)  # interactive correct + build relationships
            save_aif(aif, args.output)

            print("\n" + "=" * 50)
            print("📄 파일별 Summary")
            print("=" * 50)
            for name, data in aif["files"].items():
                if data["summary"]:
                    print(f"  {name}: {data['summary']}")

            print("\n" + "=" * 50)
            print("📋 코딩 룰")
            print("=" * 50)
            for rule in aif["rules"]:
                print(f"  - {rule}")

            print("\n" + "=" * 50)
            print("✍️  AI 가이드")
            print("=" * 50)
            print(f"  {aif['project']['prompt']}")

            print("\n" + "=" * 50)
            print("📊 토큰 분석")
            print("=" * 50)
            for model, data in aif["tokens"].items():
                print(f"  {model}: {data['original']} → {data['compressed']} ({data['saved_pct']}% 절감)")

            if args.max_tokens is not None:
                passed, actual = _check_max_tokens(aif["tokens"], args.max_tokens, args.max_tokens_model)
                if actual is None:
                    print(f"\n⚠️  --max-tokens-model '{args.max_tokens_model}'은 알 수 없는 모델입니다"
                          f" (사용 가능: {', '.join(aif['tokens'].keys())})")
                    sys.exit(1)
                elif not passed:
                    print(f"\n❌ 토큰 예산 초과: {args.max_tokens_model} 기준 {actual:,} > {args.max_tokens:,} (--max-tokens)")
                    sys.exit(1)
                else:
                    print(f"\n✅ 토큰 예산 통과: {args.max_tokens_model} 기준 {actual:,} ≤ {args.max_tokens:,}")
        elif args.max_tokens is not None:
            # pack() returned {} -- a checkpoint-and-exit on a repeated LLM
            # failure, or a cancelled/empty run -- so there's no aif["tokens"]
            # for the guard above to even check. Left unhandled, this whole
            # block (nested inside `if aif:`) never ran at all and main()
            # exited 0 by default: exactly the scenario --max-tokens exists
            # to catch (a CI pipeline silently passing despite pack() never
            # actually completing), so an incomplete pack must fail loudly
            # here too when the guard was requested. No message/exit change
            # at all when --max-tokens wasn't passed -- unchanged from before.
            print("\n❌ pack이 완료되지 않아 --max-tokens 검사를 수행할 수 없습니다"
                  " (체크포인트 저장 후 중단되었을 수 있습니다)")
            sys.exit(1)

    elif args.command == "tree":
        safe_files = _collect_and_scan(args.path)["safe"]

        # Keyed by relative_key(), not the raw file_path collect_files()
        # returns -- matching packager.py's own convention (and required
        # for the text-reference matching below: find_text_references_for_
        # file() returns entries straight from this same relative-key list,
        # which only resolve correctly against a stem_map built from those
        # same keys; see resolve_dependency()'s exact-key-match branch).
        all_names = [_rel_key(fp, args.path) for fp in safe_files]

        # Go's import paths name a *package* (a directory), not a file --
        # see go_packages.py's own docstring. Resolved once here, same as
        # packager.py's pack() -- both must call expand_go_dependencies()
        # on every .go file's raw imports, or the two commands silently
        # disagree on the same feature's output (exactly what happened to
        # the text-reference merge below before it was fixed).
        go_module_path = read_go_module_path(args.path)
        go_package_index = build_go_package_index(all_names) if go_module_path else {}

        files_data = {}
        for file_path in safe_files:
            name = _rel_key(file_path, args.path)
            deps = extract_dependencies(file_path)
            if go_module_path and file_path.endswith(".go"):
                deps = expand_go_dependencies(deps, name, go_module_path, go_package_index)
            text_refs = find_text_references_for_file(file_path, name, all_names)
            # text_dependencies recorded separately, same as packager.py's
            # own merge step -- this is what lets build_tree() tag a text
            # reference apart from a real import as internal_text_refs
            # instead of always coming back empty for this command.
            files_data[name] = {
                "dependencies": deps + text_refs,
                "text_dependencies": text_refs,
            }

        tree = build_tree(files_data)
        print_dependency_tree(tree)

    elif args.command == "search":
        safe_files = _collect_and_scan(args.path)["safe"]

        try:
            matches = search_files(
                safe_files, args.path, args.pattern,
                context_lines=args.context, ignore_case=args.ignore_case
            )
        except ValueError as e:
            print(f"⚠️  {e}")
            return

        if not matches:
            print("검색 결과 없음")
        for m in matches:
            print(f"\n{m.file}:{m.line_number}")
            for line in m.context_before:
                print(f"    {line}")
            print(f"  → {m.line}")
            for line in m.context_after:
                print(f"    {line}")

    elif args.command == "detail":
        with open(args.detail_path, "r", encoding="utf-8") as f:
            detail = json.load(f)

        entry = detail.get(args.file)
        if entry is None:
            print(f"⚠️  '{args.file}'는 {args.detail_path}에 없습니다")
            return

        print(read_detail_range(entry.get("compressed", ""), args.start, args.end))

    elif args.command == "freshness":
        with open(args.cache_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        # <name>.cache.json's sibling <name>.json (same convention
        # query_service.py's _cache_path() derives in the other direction)
        # -- read back for its own `project.scope`, so a project packed
        # with a one-off `pack --include`/`--ignore` extra doesn't get
        # diffed here against an unscoped file tree. Best-effort: any
        # naming mismatch or read failure just means no extra scope, same
        # as an aif.json packed before this field existed.
        cache_path = Path(args.cache_path)
        extra_include = extra_ignore = None
        if cache_path.name.endswith(".cache.json"):
            aif_path = cache_path.with_name(cache_path.name[: -len(".cache.json")] + ".json")
            extra_include, extra_ignore = load_pack_scope(str(aif_path))

        report = check_freshness_scoped(args.path, manifest, extra_include, extra_ignore)

        if not report.is_stale:
            print("✅ 최신 상태 — 변경된 파일 없음")
        else:
            print("⚠️  aif.json이 오래됐습니다")
            if report.changed:
                print(f"  변경됨 ({len(report.changed)}): {', '.join(report.changed)}")
            if report.added:
                print(f"  추가됨 ({len(report.added)}): {', '.join(report.added)}")
            if report.removed:
                print(f"  삭제됨 ({len(report.removed)}): {', '.join(report.removed)}")
            # Non-zero exit lets `ziplex freshness` double as a CI/PR gate --
            # fail the check when the committed aif.json has drifted from disk,
            # forcing a human to re-pack and re-review rather than merging a stale one.
            sys.exit(1)

    elif args.command == "skill":
        target = export_skill(args.aif_path, args.output)
        print(f"✅ Skill 내보내기 완료: {target}")
        print("   Claude Code가 자동으로 인식하려면 프로젝트 루트의 .claude/skills/ 아래에 있어야 합니다.")

    elif args.command == "init":
        existed = (Path(args.path) / CONFIG_FILENAME).exists()
        target = init_config(args.path)
        print(f"✅ .ziplex.json {'이미 있음' if existed else '생성됨'}: {target}")
        print('   예시: {"include": ["src/**/*.py"], "ignore": ["**/*.generated.*"]}')

    elif args.command == "settings":
        if args.settings_action == "set":
            # .strip() matches gui_server.py's POST /api/settings
            # ((data.get(field) or "").strip()) -- without it, a value
            # pasted with a trailing newline/space (common from a terminal
            # or script) would reach llm.py's Authorization header verbatim
            # and fail auth in a way that never reproduces through the GUI,
            # which already strips the same field.
            value = args.value.strip()
            if args.key == "llm_provider" and value and value not in _REAL_PROVIDER_NAMES:
                st_set.error(
                    f"알 수 없는 llm_provider: {value} (사용 가능: {', '.join(_REAL_PROVIDER_NAMES)})"
                )
            current = app_settings.load_settings()
            current[args.key] = value
            app_settings.save_settings(current)
            shown = _mask_secret(value) if args.key in _SECRET_FIELDS and value else (value or "(미설정)")
            print(f"✅ {args.key} = {shown} (저장됨: {app_settings.SETTINGS_PATH})")
        else:  # "get" or omitted -- ziplex settings alone is the read path
            _print_settings(app_settings.load_settings())

    elif args.command == "checkpoint":
        if args.checkpoint_action == "clean":
            if args.all:
                removed = app_checkpoint.clear_all_checkpoints()
                print(f"🗑️  체크포인트 {removed}개 삭제됨")
            elif args.path:
                existed = app_checkpoint.load_checkpoint(args.path) is not None
                app_checkpoint.delete_checkpoint(args.path)
                print(f"🗑️  체크포인트 삭제됨: {args.path}" if existed else f"체크포인트 없음: {args.path}")
            else:
                # Neither `path` nor `--all` given -- an ambiguous "clean
                # what?" rather than a silent no-op. ckp_clean.error() (not
                # a plain print+sys.exit) matches every other invalid-usage
                # message in this CLI, which argparse itself already
                # renders this way for its own choices=/required checks.
                ckp_clean.error("삭제할 프로젝트 경로 또는 --all 중 하나가 필요합니다")
        else:  # "list" or omitted -- ziplex checkpoint alone is the read path
            _print_checkpoints(app_checkpoint.list_checkpoints())

    elif args.command == "doctor":
        _print_doctor(app_doctor.run_diagnostics(args.path))

    elif args.command == "link":
        _edit_saved_relationship(args.aif_path, args.file, args.target, add_relationship, "연결됨")

    elif args.command == "unlink":
        _edit_saved_relationship(args.aif_path, args.file, args.target, remove_relationship, "연결 해제됨")

    else:
        _print_command_overview()

if __name__ == "__main__":
    main()