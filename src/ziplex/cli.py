import argparse
import os
import json
import re
import sys
from .core.graph import build_graph
from datetime import datetime
from pathlib import Path
from typing import Callable

from .extract.code.extractor import extract_signatures, extract_dependencies, extract_api, debug_tree
from .extract.code.compressor import compress_file
from .file.collector import collect_files, print_tree as print_file_tree
from .file.scanner import scan_files
from .file.media import classify_media_file
from .file.textutil import relative_key as _rel_key, normalize_path
from .tokenizer import analyze_tokens_with_compression
from .enrich.llm import LANGUAGE_NAMES, DEFAULT_PROVIDER_NAME, PROVIDERS, GeminiProvider, OpenAIProvider, ClaudeProvider, get_usage
from .packager import pack, save_aif, resolve_output_path
from .enrich import summarizer
from .file.relationship import print_tree as print_dependency_tree
from .search import search_files, read_detail_range
from .freshness import check_freshness_scoped, load_pack_scope, scope_from_aif, cache_path_for_aif, aif_path_for_cache
from .skill_export import (
    export_skill, resolve_skill_target, read_existing_skill_project_name, resolve_skill_display_name,
)
from .flat_export import export_flat
from .config import init_config, CONFIG_FILENAME, collection_kwargs as _collection_kwargs, collect_and_scan as _collect_and_scan
from . import __version__
from . import settings as app_settings
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
    ("impact [<file...>] [--diff REF]", "Tests affected by a change, plus the command to run them"),
    ("deps <file...> [--in]", "What a file imports, or (--in) what depends on it"),
    ("path <a> <b>", "Shortest import chain between two files"),
    ("index [<path>] [--check]", "Refresh the dependency-graph index (.ziplex/index.db)"),
    ("install claude [--hook]", "Register the MCP server (and the post-edit hook) with Claude Code"),
    ("tree <path>", "Print the dependency tree"),
    ("pack <path>", "Pack the project into aif.json (summaries, rules, guide; --no-llm for no API key)"),
    ("freshness <path> <name>.cache.json", "Hash-check a committed aif.json against disk"),
    ("skill <name>.json / flatten <name>.json", "Export a pack as a Claude Agent Skill / one Markdown file"),
    ("init <path>", "Scaffold .ziplex.json (include/ignore glob patterns)"),
    ("settings [set <key> <value>]", "View/change ~/.ziplex/settings.json (LLM provider, keys)"),
    ("doctor [<path>]", "Environment sanity check"),
    ("dev <tool> ...", "Debug tools: collect, tokens, search, detail, signatures, dependencies, api, compress, debug"),
]


def _print_command_overview() -> None:
    print(f"Ziplex {__version__} -- which tests a change affects, and what depends on what (zero-config dependency graph)\n")
    print("Usage: ziplex <command> [options]\n")
    width = max(len(cmd) for cmd, _ in _COMMAND_OVERVIEW)
    for cmd, desc in _COMMAND_OVERVIEW:
        print(f"  {cmd.ljust(width)}  {desc}")
    print("\nRun `ziplex <command> --help` for a command's full options, or `ziplex-gui` for the GUI.")


def _require_dir_or_exit(path: str) -> None:
    """Guard for every command taking a project-folder positional (`path`)
    that goes straight into collect_files()/collect_and_scan(). Without
    this, a typo'd path doesn't error at all -- collect_files() just walks
    zero files and every downstream step (scan/tokenize/pack/...) silently
    reports "0 files"/"파일 없음" with exit 0, giving no signal that the
    path itself was wrong rather than the project genuinely being empty
    (confirmed directly: `ziplex tokens <typo'd path>` prints a full,
    innocent-looking 0-token breakdown for every model). `init` is the one
    exception that used to crash instead -- write_text() into a directory
    that was never created raises an uncaught FileNotFoundError; this same
    guard fixes that case too by catching it earlier, with a real message.
    `doctor <path>` deliberately isn't routed through this -- it already
    reports a missing directory as one line in its own diagnostic report
    rather than refusing to run.
    """
    if not Path(path).is_dir():
        print(f"❌ 디렉터리를 찾을 수 없습니다: {path}")
        sys.exit(1)


def _require_file_or_exit(path: str) -> None:
    """Guard for the single-file extraction commands (compress/signatures/
    dependencies/api/debug). extract/code/*'s read_text() deliberately
    returns None (not an exception) for any unreadable path -- by design,
    for the main collect->scan->extract pipeline where every path already
    came from a real directory listing, so this never fires there. But
    these CLI commands hand it a user-typed path directly, and a missing
    file used to reach that same None branch: every one of them prints
    nothing at all for signatures/dependencies/api/compress (an empty
    list/string, exit 0 -- confirmed directly, indistinguishable from a
    real file that's just empty) and a misleading "텍스트로 읽을 수 없는
    파일입니다" (implies binary, not missing) for debug.
    """
    if not Path(path).is_file():
        print(f"❌ 파일을 찾을 수 없습니다: {path}")
        sys.exit(1)


def _load_json_or_exit(path: str) -> dict:
    """Shared read path for every CLI command that loads an already-produced
    JSON file (aif.json/detail.json/cache.json) by path -- a friendly
    "❌ ... 읽기 실패" + exit 1, matching every other invalid-usage message
    in this CLI, instead of an uncaught FileNotFoundError/JSONDecodeError
    traceback reaching the user raw. Originally only `_edit_saved_relationship`
    had this guard; `detail`/`freshness` opened their own file with a bare
    `open()` and crashed on a typo'd path -- a real, reproducible inconsistency
    (link/unlink handled it, detail/freshness didn't) rather than a
    hypothetical one.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ {path} 읽기 실패: {e}")
        sys.exit(1)


def _check_max_tokens(aif_tokens: dict, max_tokens: int, model: str) -> tuple[bool, int | None]:
    """CI-guard check for `pack --max-tokens`: does the packed payload
    (aif.json's actual per-file token cost, tokenizer.py's "compressed"
    figure -- see analyze_tokens_with_payload) fit under a budget?

    Returns (passed, actual_count). actual_count is None (and passed is
    always False) when `model` isn't a key in aif_tokens (aif["tokens"],
    keyed by tokenizer.MODEL_MAX_TOKENS) -- lets the caller tell "over
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


def _model_label(model: str, data: dict) -> str:
    """`model`, with a "(근사치)" suffix when its token count is a
    character-based estimate rather than an exact tiktoken one
    (data["approx"] -- see tokenizer.is_approx_model()). The one shared
    place every token-count print site builds this label, so a reader never
    sees an approximate figure (Claude/Gemini) presented with the same
    confidence as a real count -- including the --max-tokens pass/fail
    line, which used to build this inline and separately from the two
    breakdown-table print sites, letting it drift out of sync with them."""
    return f"{model} (근사치)" if data.get("approx") else model


def _build_parser() -> argparse.ArgumentParser:
    """Builds and returns the fully-configured argparse parser -- every
    subcommand's own positional/optional arguments, nothing dispatch-
    related. Split out of main() so parser construction (declarative,
    ~130 lines) and command dispatch (what actually runs, one `_cmd_*`
    function per subcommand below) can be read, tested, and changed
    independently -- main() itself is now just "parse argv, look up a
    handler, call it."

    Two subparsers (`checkpoint clean`, `settings set`) need to raise a
    usage error from their own command handler, after parsing, once a
    runtime-only condition (not expressible via a plain `choices=`) fails.
    `set_defaults()` stashes each one onto `args` under a private key,
    present only when that specific subcommand was actually selected, so
    the matching `_cmd_*` function below can call `.error()` on the exact
    subparser argparse itself would have used, without threading a second
    lookup structure through every handler's signature.
    """
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
    # Kept so old scripts keep working: pack is always non-interactive now.
    p.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--auto-correct", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--no-cache", action="store_true", help="변경 없는 파일도 요약을 다시 생성 (이전 pack 재사용 끄기)")
    p.add_argument("--no-llm", action="store_true",
                    help="LLM 호출 없이 구조 정보(시그니처/의존성)만으로 패킹 -- GEMINI_API_KEY 불필요, rules/AI 가이드는 생성되지 않음")
    p.add_argument("--include", default=None, help="포함할 glob 패턴, 쉼표로 구분 (예: 'src/**/*.py,*.md') -- .ziplex.json의 include에 추가됨")
    p.add_argument("--ignore", default=None, help="추가로 제외할 glob 패턴, 쉼표로 구분 -- .ziplex.json의 ignore에 추가됨")
    p.add_argument("--max-tokens", type=int, default=None, metavar="N",
                    help="패킹된 aif.json의 파일별 payload가 N 토큰을 넘으면 종료 코드 1로 실패 -- CI에서 컨텍스트 예산 초과를 막는 용도")
    p.add_argument("--max-tokens-model", default="GPT-4o", metavar="MODEL",
                    help="--max-tokens 판단 기준 모델 (기본값: GPT-4o, tokenizer.MODEL_MAX_TOKENS의 키 중 하나 -- "
                         "Claude/Gemini는 tiktoken 인코딩이 없어 문자 수 기반 근사치)")
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
    sk.add_argument(
        "--project",
        default=None,
        help="프로젝트 폴더 경로 -- 주면 내보내기 전에 무료 freshness 체크(LLM 호출 없음)를 한 번 하고, "
        "aif.json이 오래됐으면 경고만 하고 계속 내보냅니다 (막지는 않음)",
    )

    fl = sub.add_parser(
        "flatten",
        help="aif.json+detail.json을 파일 하나(Markdown, 요약+압축코드 인라인)로 합쳐서 내보내기 -- "
        "도구/MCP 서버 없이 채팅에 통째로 붙여넣는 용도",
    )
    fl.add_argument("aif_path", help="aif.json 경로")
    fl.add_argument("--output", "-o", default=None, help="출력 파일 경로 (기본값: <name>.flat.md)")

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
    st_set.set_defaults(_settings_set_parser=st_set)

    dc = sub.add_parser(
        "doctor",
        help="환경 점검 -- Python 버전/활성 LLM provider/API key/secretlint/남은 체크포인트를 한 번에 확인 (LLM 호출 없음)",
    )
    dc.add_argument("path", nargs="?", default=None, help="선택: 프로젝트 폴더 경로 (주면 .env/git 저장소 여부도 확인)")

    ix = sub.add_parser("index", help="의존성 그래프 인덱스(.ziplex/index.db)를 디스크와 동기화 (LLM 호출 없음)")
    ix.add_argument("path", nargs="?", default=".", help="프로젝트 폴더 경로 (기본값: 현재 폴더)")
    ix.add_argument("--check", action="store_true",
                    help="갱신 후 추가/변경/삭제된 파일이 있으면 종료 코드 1 (인덱스가 처음 만들어진 경우 포함)")
    ix.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")

    dp = sub.add_parser("deps", help="파일이 import하는 파일 (기본) 또는 --in: 그 파일에 의존하는 파일")
    dp.add_argument("files", nargs="+", help="파일 경로 (상대/절대 경로 또는 유일한 접미사, 예: core/graph.py)")
    dp.add_argument("--project", default=".", help="프로젝트 폴더 경로 (기본값: 현재 폴더)")
    dp.add_argument("--in", dest="incoming", action="store_true", help="역방향: 이 파일에 의존하는 파일 (hop별)")
    dp.add_argument("--depth", type=int, default=1, help="--in의 탐색 깊이 (기본값: 1)")
    dp.add_argument("--mentions", action="store_true", help="문서 속 산문 언급(text reference)도 포함")
    dp.add_argument("--external", action="store_true", help="외부 패키지 import도 출력 (정방향만)")
    dp.add_argument("--json", action="store_true", help="쿼리 결과를 JSON으로 출력")

    im = sub.add_parser("impact", help="변경된 파일이 영향을 주는 테스트와 실행 명령 (인자 없으면 --diff worktree)")
    im.add_argument("files", nargs="*", help="변경된 파일 경로")
    im.add_argument("--project", default=".", help="프로젝트 폴더 경로 (기본값: 현재 폴더)")
    im.add_argument("--diff", default=None, metavar="REF",
                    help="git에서 변경 파일을 가져옴: worktree | staged | HEAD~1 | main... 등")
    im.add_argument("--format", choices=["text", "json", "paths", "commands"], default="text",
                    help="text(기본) | json | paths(테스트 경로 한 줄씩) | commands(실행 명령 한 줄씩). "
                         "전체 실행이 필요하면 paths는 아무것도 출력하지 않고 commands는 전체 실행 명령을 출력")

    pa = sub.add_parser("path", help="두 파일 사이의 최단 import 경로 (없으면 종료 코드 1)")
    pa.add_argument("source", help="시작 파일")
    pa.add_argument("target", help="도착 파일")
    pa.add_argument("--project", default=".", help="프로젝트 폴더 경로 (기본값: 현재 폴더)")

    hk = sub.add_parser("hook", help="Claude Code 훅 진입점 (stdin으로 hook JSON을 받음, 항상 종료 코드 0)")
    hk.add_argument("event", choices=["post-edit"], help="post-edit: PostToolUse(Edit|Write|MultiEdit)")

    ins = sub.add_parser("install", help="Claude Code에 Ziplex 등록 (MCP 서버, 선택적으로 편집 후 훅)")
    ins.add_argument("target", choices=["claude"], help="설치 대상")
    ins.add_argument("--project", default=".", help="프로젝트 폴더 경로 (기본값: 현재 폴더)")
    ins.add_argument("--hook", action="store_true",
                     help="<project>/.claude/settings.json에 PostToolUse 훅(ziplex-hook)도 추가")
    ins.add_argument("--dry-run", action="store_true", help="실행/저장하지 않고 할 일만 출력")

    return parser


def _cmd_compress(args) -> None:
    _require_file_or_exit(args.file)
    print(compress_file(args.file))


def _cmd_signatures(args) -> None:
    _require_file_or_exit(args.file)
    sigs = extract_signatures(args.file)
    for sig in sigs:
        print(f"  {sig}")


def _cmd_dependencies(args) -> None:
    _require_file_or_exit(args.file)
    deps = extract_dependencies(args.file)
    for dep in deps:
        print(f"  {dep}")


def _cmd_api(args) -> None:
    _require_file_or_exit(args.file)
    apis = extract_api(args.file)
    for api in apis:
        print(f"  {api}")


def _cmd_debug(args) -> None:
    _require_file_or_exit(args.file)
    debug_tree(args.file)


def _cmd_collect(args) -> None:
    _require_dir_or_exit(args.path)
    files = collect_files(args.path, **_collection_kwargs(args.path))
    scan_result = scan_files(files, root=args.path)

    print(f"\n📁 수집된 파일: {len(files)}개")
    print_file_tree(files, args.path)

    if scan_result["dangerous"]:
        print(f"\n⚠️  민감 파일 감지: {len(scan_result['dangerous'])}개")
        for d in scan_result["dangerous"]:
            print(f"  ❌ {d['file']} -- {d.get('reason') or '민감 정보로 추정됨'}")

    print(f"\n✅ 안전한 파일: {len(scan_result['safe'])}개")


def _cmd_tokens(args) -> None:
    _require_dir_or_exit(args.path)
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
        print(_model_label(model, data))
        print(f"  압축 전: {data['original']:,} / {data['max']:,} {data['original_bar']}")
        print(f"  압축 후: {data['compressed']:,} / {data['max']:,} {data['compressed_bar']}")
        print(f"  절감:    {data['saved']:,} 토큰 ({data['saved_pct']}% 감소)\n")


def _enforce_max_tokens(aif: dict, args) -> None:
    # aif["tokens"]'s "compressed" is the saved aif.json's own size
    # (packager._measure_saved_tokens(), run by save_aif()).
    passed, actual = _check_max_tokens(aif["tokens"], args.max_tokens, args.max_tokens_model)
    if actual is None:
        print(f"\n⚠️  --max-tokens-model '{args.max_tokens_model}'은 알 수 없는 모델입니다"
              f" (사용 가능: {', '.join(aif['tokens'].keys())})")
        sys.exit(1)
    # actual isn't None, so args.max_tokens_model is a real key --
    # safe to look up its data for the same approx label the two
    # breakdown tables above already show for this model.
    model_label = _model_label(args.max_tokens_model, aif["tokens"][args.max_tokens_model])
    if not passed:
        print(f"\n❌ 토큰 예산 초과: {model_label} 기준 {actual:,} > {args.max_tokens:,} (--max-tokens)")
        sys.exit(1)
    else:
        print(f"\n✅ 토큰 예산 통과: {model_label} 기준 {actual:,} ≤ {args.max_tokens:,}")


def _up_to_date_pack(args) -> tuple[Path, dict] | None:
    """(aif path, aif) when the previous pack at the output path is exactly
    what this --auto run would produce again -- same Ziplex version, LLM
    mode, language and scope, no failed summaries, and no file changed on
    disk. None means "pack normally". Interactive selection and --no-cache
    never short-circuit."""
    if not args.auto or args.no_cache:
        return None
    name = Path(args.path).resolve().name
    aif_path = resolve_output_path({"project": {"name": name}}, args.output, project_path=args.path)
    try:
        aif = json.loads(aif_path.read_text(encoding="utf-8"))
        manifest = json.loads(cache_path_for_aif(str(aif_path)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    project = aif.get("project") or {}
    include, ignore, deselected = scope_from_aif(aif)
    lang = args.lang if args.lang in LANGUAGE_NAMES else "en"
    if (
        project.get("ziplex_version") != __version__
        or project.get("llm") != (not args.no_llm)
        or project.get("language") != lang
        or deselected
        or (include or []) != (_split_patterns(args.include) or [])
        or (ignore or []) != (_split_patterns(args.ignore) or [])
        or any(summarizer.is_summary_failed_placeholder(f.get("summary", "")) for f in aif.get("files", {}).values())
    ):
        return None
    if check_freshness_scoped(args.path, manifest, include, ignore).is_stale:
        return None
    return aif_path, aif


def _cmd_pack(args) -> None:
    _require_dir_or_exit(args.path)
    if not args.no_llm:
        from .enrich import llm
        if llm.requests is None and llm.describe_active_provider().get("name") != "mock":
            sys.exit(f"❌ {llm.MISSING_EXTRA_MESSAGE} -- 또는 `ziplex pack --no-llm`")
    up_to_date = _up_to_date_pack(args)
    if up_to_date:
        aif_path, aif = up_to_date
        print(f"✅ 변경 없음 -- 기존 pack이 최신입니다: {aif_path} (다시 하려면 --no-cache)")
        if args.max_tokens is not None:
            _enforce_max_tokens(aif, args)
        return
    aif = pack(
        args.path, use_cache=not args.no_cache,
        use_llm=not args.no_llm,
        include=_split_patterns(args.include),
        ignore=_split_patterns(args.ignore),
        lang=args.lang,
        # Where `-o` will save, so a repeat run with the same `-o` finds the
        # previous pack there and reuses unchanged summaries instead of
        # re-summarizing every file (pack() otherwise only looks in
        # <project>/.ziplex/).
        result_dir=Path(args.output).parent if args.output else None,
    )
    if aif:
        save_aif(aif, args.output, project_path=args.path)

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
            print(f"  {_model_label(model, data)}: {data['original']} → {data['compressed']} ({data['saved_pct']}% 절감)")

        # Real, provider-billed usage for every generate() call this run
        # actually made -- a different number from the "토큰 분석" block
        # above, which only measures the *packed artifact's* size (original
        # file text vs. the final summary), never what the LLM API itself
        # was actually billed to produce it (every batched prompt's full
        # input, not just the short final answer). All-zero and skipped
        # under --no-llm/LLM_PROVIDER=mock, correctly -- no real API call
        # happened, so there's nothing to report.
        usage = get_usage()
        if usage["calls"]:
            print("\n" + "=" * 50)
            print("📡 LLM 실사용량 (API 과금 기준, 패킹 결과물 크기와는 별개)")
            print("=" * 50)
            print(f"  요청 수: {usage['calls']}")
            print(f"  입력 토큰: {usage['input_tokens']:,}")
            print(f"  출력 토큰: {usage['output_tokens']:,}")
            print(f"  합계: {usage['total_tokens']:,}")

        if args.max_tokens is not None:
            _enforce_max_tokens(aif, args)
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


def _cmd_tree(args) -> None:
    _require_dir_or_exit(args.path)
    safe_files = _collect_and_scan(args.path)["safe"]
    print_dependency_tree(build_graph(args.path, safe_files))


def _cmd_search(args) -> None:
    _require_dir_or_exit(args.path)
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


def _cmd_detail(args) -> None:
    detail = _load_json_or_exit(args.detail_path)

    # normalize_path(): a Windows `src\a.py` (or `./src/a.py`) names the
    # same '/'-keyed entry -- same rule as query_service.get_detail().
    file = normalize_path(args.file)
    entry = detail.get(file)
    if entry is None:
        print(f"⚠️  '{file}'는 {args.detail_path}에 없습니다")
        sys.exit(1)

    print(read_detail_range(entry.get("compressed", ""), args.start, args.end))


def _cmd_freshness(args) -> None:
    _require_dir_or_exit(args.path)
    manifest = _load_json_or_exit(args.cache_path)

    # <name>.cache.json's sibling <name>.json (freshness.aif_path_for_cache(),
    # the reverse of _cmd_skill()'s own cache_path_for_aif() call below) --
    # read back for its own `project.scope`, so a project packed with a
    # one-off `pack --include`/`--ignore` extra doesn't get diffed here
    # against an unscoped file tree. Best-effort: any naming mismatch or
    # read failure just means no extra scope, same as an aif.json packed
    # before this field existed.
    extra_include = extra_ignore = deselected = None
    aif_path = aif_path_for_cache(args.cache_path)
    if aif_path is not None:
        extra_include, extra_ignore, deselected = load_pack_scope(str(aif_path))

    report = check_freshness_scoped(args.path, manifest, extra_include, extra_ignore, deselected)

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


def _cmd_skill(args) -> None:
    # Real gap found by code review: export_skill() (skill_export.py) never
    # took a project_path at all, so nothing here could ever tell a human
    # their aif.json had drifted from disk before baking that stale snapshot
    # into an always-committed skill directory -- every other read surface
    # (CLI freshness, MCP check_freshness, the GUI's badges/watcher) already
    # has this same free (no LLM call) check, skill export was the one gap.
    # --project is optional and this only ever warns, never blocks: someone
    # exporting a known-slightly-stale-but-good-enough skill on purpose
    # shouldn't be stopped, and skill_export.py itself stays freshness-
    # agnostic (a deliberate split, not an oversight -- see its own
    # docstring) rather than gaining a second responsibility.
    # Parsed once and reused for the freshness-scope lookup below, the
    # collision check, and export_skill() itself -- a real gap found by
    # code review: this same aif_path used to be read/parsed up to three
    # separate times in one invocation.
    try:
        aif = json.loads(Path(args.aif_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        aif = None

    if args.project:
        _require_dir_or_exit(args.project)
        cache_path = cache_path_for_aif(args.aif_path)
        # Best-effort, same as _cmd_freshness()'s own sibling-file lookup --
        # a missing/corrupt cache.json (an aif.json moved/renamed away from
        # its siblings, or one produced by a pre-cache.json version of
        # Ziplex) just means this check is skipped, not that the export
        # itself should fail.
        try:
            manifest = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None
        if manifest is not None:
            # (None, None) when aif itself failed to parse above, same as
            # load_pack_scope()'s own failure fallback used to give.
            extra_include, extra_ignore, deselected = scope_from_aif(aif) if aif is not None else (None, None, None)
            report = check_freshness_scoped(args.project, manifest, extra_include, extra_ignore, deselected)
            if report.is_stale:
                print("⚠️  내보내려는 aif.json이 프로젝트의 현재 상태와 다릅니다 (오래됨) -- 그대로 내보냅니다.")
                if report.changed:
                    print(f"   변경됨 ({len(report.changed)}): {', '.join(report.changed)}")
                if report.added:
                    print(f"   추가됨 ({len(report.added)}): {', '.join(report.added)}")
                if report.removed:
                    print(f"   삭제됨 ({len(report.removed)}): {', '.join(report.removed)}")
                print("   최신 상태로 내보내려면 `ziplex pack`을 다시 실행한 뒤 이 명령을 재실행하세요.")

    # Real gap found by code review: no collision guard at all -- two
    # differently-named projects that happen to slugify to the same
    # directory name (a monorepo packing multiple subprojects with generic
    # names like "backend"/"api" is a real, plausible way to hit this)
    # silently overwrote each other's skill directory with no warning.
    # Detecting it needs the *display* name from the existing SKILL.md's
    # own heading, not the slug or frontmatter `name:` -- both of those are
    # identical by construction whenever two projects actually collide (see
    # read_existing_skill_project_name()'s own docstring). Best-effort and
    # warn-only, the same "never block a real export" philosophy as the
    # freshness check above.
    if aif is not None:
        target_for_collision_check = resolve_skill_target(aif, args.output)
        existing_name = read_existing_skill_project_name(target_for_collision_check)
        new_name = resolve_skill_display_name(aif)
        if existing_name and existing_name != new_name:
            print(
                f"⚠️  {target_for_collision_check}에 다른 프로젝트('{existing_name}')의 skill이 "
                "이미 있습니다 -- 덮어씁니다."
            )

    # export_skill() does its own internal open()/json.load() on aif_path
    # (and, best-effort, its sibling detail.json) whenever aif is None --
    # here it's already been parsed above, so passing it through skips that
    # read. Still wrapped rather than routed via _load_json_or_exit() since
    # that function itself needs the raw path, not a pre-parsed dict. Same
    # "❌ ... 읽기 실패" shape as every other file-loading command in this
    # CLI -- reachable here whenever the read above already failed (aif is
    # None, so export_skill() redoes it and raises the same error).
    try:
        target = export_skill(args.aif_path, args.output, aif=aif)
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ {args.aif_path} 읽기 실패: {e}")
        sys.exit(1)
    print(f"✅ Skill 내보내기 완료: {target}")
    print("   Claude Code가 자동으로 인식하려면 프로젝트 루트의 .claude/skills/ 아래에 있어야 합니다.")


def _cmd_flatten(args) -> None:
    # export_flat() does its own open()/json.load() on aif_path and its
    # sibling detail.json (via query_service._detail_path(), the same
    # shared convention skill_export.py already reuses) -- both wrapped
    # here in one try/except, same "❌ ... 읽기 실패" shape as _cmd_skill's
    # own aif_path failure and every other file-loading command in this CLI.
    # A missing detail.json specifically (aif.json moved away from its
    # sibling, or a pre-detail.json-era pack) surfaces as the same message
    # rather than a separate one -- either way there's nothing to export.
    try:
        target = export_flat(args.aif_path, args.output)
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ {args.aif_path} (또는 그 형제 detail.json) 읽기 실패: {e}")
        sys.exit(1)
    print(f"✅ 단일 파일 내보내기 완료: {target}")
    print("   도구 접근 없는 채팅(웹 UI 등)에 이 파일 하나만 통째로 붙여넣으면 됩니다.")


def _cmd_init(args) -> None:
    _require_dir_or_exit(args.path)
    existed = (Path(args.path) / CONFIG_FILENAME).exists()
    target = init_config(args.path)
    print(f"✅ .ziplex.json {'이미 있음' if existed else '생성됨'}: {target}")
    print('   예시: {"include": ["src/**/*.py"], "ignore": ["**/*.generated.*"]}')


def _cmd_settings(args) -> None:
    if args.settings_action == "set":
        # .strip() matches gui_server.py's POST /api/settings
        # ((data.get(field) or "").strip()) -- without it, a value
        # pasted with a trailing newline/space (common from a terminal
        # or script) would reach llm.py's Authorization header verbatim
        # and fail auth in a way that never reproduces through the GUI,
        # which already strips the same field.
        value = args.value.strip()
        if args.key == "llm_provider" and value and value not in _REAL_PROVIDER_NAMES:
            args._settings_set_parser.error(
                f"알 수 없는 llm_provider: {value} (사용 가능: {', '.join(_REAL_PROVIDER_NAMES)})"
            )
        current = app_settings.load_settings()
        current[args.key] = value
        app_settings.save_settings(current)
        shown = _mask_secret(value) if args.key in _SECRET_FIELDS and value else (value or "(미설정)")
        print(f"✅ {args.key} = {shown} (저장됨: {app_settings.SETTINGS_PATH})")
    else:  # "get" or omitted -- ziplex settings alone is the read path
        _print_settings(app_settings.load_settings())


def _cmd_doctor(args) -> None:
    _print_doctor(app_doctor.run_diagnostics(args.path))


def _open_graph(project: str):
    from .core.api import Graph  # deferred: keeps `ziplex hook` and unrelated commands light
    if not Path(project).is_dir():
        print(f"❌ 디렉터리를 찾을 수 없습니다: {project}", file=sys.stderr)
        sys.exit(2)
    return Graph(project)


def _usage_error(message: str) -> None:
    print(f"❌ {message}", file=sys.stderr)
    sys.exit(2)


def _cmd_index(args) -> None:
    import time
    graph = _open_graph(args.path)
    was_new = graph.index.meta("generation") is None
    started = time.perf_counter()
    result = graph.refresh()
    elapsed = time.perf_counter() - started
    conn = graph.index.conn
    files = conn.execute("SELECT COUNT(*) FROM files WHERE kind IN ('code', 'text', 'media')").fetchone()[0]
    edges, internal = conn.execute("SELECT COUNT(*), COUNT(dst_id) FROM edges").fetchone()
    dirty = result["added"] + result["changed"] + result["removed"]
    if args.json:
        print(json.dumps({
            **result, "new_index": was_new, "files": files, "edges": internal,
            "external_edges": edges - internal, "seconds": round(elapsed, 3),
            "db": str(graph.index.db_path),
        }))
    else:
        print(
            f"index{' (new)' if was_new else ''}: {files} files, {internal} edges (+{edges - internal} external) | "
            f"+{result['added']} ~{result['changed']} -{result['removed']} | {elapsed:.2f}s"
        )
    if args.check and dirty:
        if not args.json:
            print(f"⚠️  index was out of date ({dirty} file(s) differed from disk)", file=sys.stderr)
        sys.exit(1)


def _cmd_deps(args) -> None:
    from .core import api
    graph = _open_graph(args.project)
    graph.refresh()
    try:
        if args.incoming:
            result = graph.dependents(args.files, depth=args.depth, include_mentions=args.mentions)
        else:
            result = graph.dependencies(args.files, external=args.external)
            if not args.mentions:
                for entry in result.values():
                    entry.pop("mentions", None)
    except ValueError as e:
        _usage_error(str(e))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(api.render_dependents(result) if args.incoming else api.render_dependencies(result))


def _run_all_commands(graph) -> list[str]:
    """Whole-suite commands, one per ecosystem that has indexed tests."""
    from .core.testmap import run_command
    tests = [p for (p,) in graph.index.conn.execute("SELECT path FROM files WHERE is_test = 1")]
    commands = []
    if any(p.endswith(".py") for p in tests):
        commands.append("pytest")
    js = [p for p in tests if re.search(r"\.[cm]?[jt]sx?$", p)]
    if js:  # the runner run_command() would pick, without file arguments
        commands.append(run_command(js[:1], _package_json(graph.root))[0].rsplit(" ", 1)[0])
    if any(p.endswith(".go") for p in tests):
        commands.append("go test ./...")
    return commands


def _package_json(root: str) -> dict | None:
    try:
        with open(Path(root) / "package.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _cmd_impact(args) -> None:
    """Exit 0 unless the arguments are unusable. When a targeted selection
    is unsafe (`run_all` non-empty: build/config/lockfile changed, or a file
    was deleted), `paths` prints nothing -- so `pytest $(ziplex impact
    --format paths)` runs the whole suite -- and `commands` prints the
    whole-suite commands; both explain why on stderr."""
    from .core import api
    graph = _open_graph(args.project)
    graph.refresh()
    diff = args.diff or (None if args.files else "worktree")
    try:
        result = graph.impacted_tests(args.files, diff=diff)
    except ValueError as e:
        _usage_error(str(e))
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.format == "text":
        print(api.render_impacted(result))
        return
    if result["not_indexed"]:
        print("ziplex: not in the index: " + ", ".join(result["not_indexed"]), file=sys.stderr)
    if result["run_all"]:
        print("ziplex: run all tests -- " + "; ".join(result["run_all"]), file=sys.stderr)
        if args.format == "commands":
            for cmd in _run_all_commands(graph):
                print(cmd)
        return
    if not result["tests"]:
        print("ziplex: no tests affected", file=sys.stderr)
    # CI-facing formats include the "possibly affected" tests too.
    for line in ([t["file"] for t in result["tests"]] if args.format == "paths" else result["commands_all"]):
        print(line)


def _cmd_path(args) -> None:
    from .core import api
    graph = _open_graph(args.project)
    graph.refresh()
    try:
        hops = graph.path(args.source, args.target)
        source, target = graph.resolve(args.source), graph.resolve(args.target)
    except ValueError as e:
        _usage_error(str(e))
    print(api.render_path(source, target, hops))
    if hops is None:
        sys.exit(1)


def _cmd_hook(args) -> None:
    from . import hook
    if hasattr(sys.stdin, "reconfigure"):  # Claude Code sends UTF-8, not the console codepage
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(hook.run(sys.stdin, sys.stdout))


HOOK_COMMAND = "ziplex-hook"
# Commands an existing settings.json may already run for this hook.
_HOOK_COMMANDS = {HOOK_COMMAND, "ziplex hook post-edit"}
HOOK_ENTRY = {"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 10}]}


def merge_hook_settings(settings: dict) -> tuple[dict, bool]:
    """`settings` with the post-edit PostToolUse entry added; (new, changed).
    Existing keys and hooks are kept; an entry already running
    HOOK_COMMAND (under any matcher) counts as installed."""
    import copy
    merged = copy.deepcopy(settings)
    post = merged.setdefault("hooks", {}).setdefault("PostToolUse", [])
    for entry in post:
        if any(isinstance(h, dict) and h.get("command") in _HOOK_COMMANDS for h in (entry.get("hooks") or [])):
            return merged, False
    post.append(copy.deepcopy(HOOK_ENTRY))
    return merged, True


def _cmd_install(args) -> None:
    import difflib
    import shutil
    import subprocess

    project = Path(args.project).resolve()
    if not project.is_dir():
        _usage_error(f"디렉터리를 찾을 수 없습니다: {args.project}")

    mcp_cmd = ["claude", "mcp", "add", "ziplex", "--", "ziplex-mcp", "--project", str(project)]
    shown = " ".join(f'"{a}"' if " " in a else a for a in mcp_cmd)
    claude = shutil.which("claude")
    if args.dry_run or claude is None:
        reason = "--dry-run" if args.dry_run else "claude CLI를 PATH에서 찾지 못했습니다"
        print(f"ℹ️  MCP 등록 명령 ({reason} -- 직접 실행하세요):\n  {shown}")
    else:
        print(f"▶ {shown}")
        done = subprocess.run([claude, *mcp_cmd[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace")
        output = (done.stdout + done.stderr).strip()
        if output:
            print("  " + output.replace("\n", "\n  "))
        if done.returncode != 0:
            print("⚠️  MCP 등록 실패 (이미 등록돼 있으면 무시해도 됩니다)")

    if args.hook:
        settings_path = project / ".claude" / "settings.json"
        try:
            before_text = settings_path.read_text(encoding="utf-8") if settings_path.exists() else ""
            before = json.loads(before_text) if before_text.strip() else {}
        except (OSError, ValueError) as e:
            _usage_error(f"{settings_path} 읽기 실패 (수정하지 않음): {e}")
        if not isinstance(before, dict):
            _usage_error(f"{settings_path}의 최상위가 JSON 객체가 아닙니다 (수정하지 않음)")
        merged, changed = merge_hook_settings(before)
        if not changed:
            print(f"✅ 훅이 이미 설치돼 있습니다: {settings_path}")
        else:
            after_text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
            diff = difflib.unified_diff(
                before_text.splitlines(keepends=True), after_text.splitlines(keepends=True),
                fromfile=str(settings_path) if before_text else "/dev/null", tofile=str(settings_path),
            )
            sys.stdout.writelines(diff)
            if args.dry_run:
                print("ℹ️  --dry-run: settings.json은 저장하지 않았습니다")
            else:
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(after_text, encoding="utf-8")
                print(f"✅ PostToolUse 훅 추가됨: {settings_path}")

    if args.dry_run:
        print(f"ℹ️  --dry-run: 인덱스는 만들지 않았습니다 (ziplex index {project})")
        return
    graph = _open_graph(str(project))
    result = graph.refresh()
    print(f"✅ 인덱스 갱신됨: {graph.index.db_path} (+{result['added']} ~{result['changed']} -{result['removed']})")


# One entry per registered subcommand -- main() just looks up args.command
# here and calls the match. A bare invocation (args.command is None, no
# subcommand typed at all) is the one case with no entry, handled by
# main() itself via _print_command_overview() rather than a dict.get()
# default, since that function takes no args (a real command handler
# always does) and forcing a matching signature onto it just to fit this
# dict would be a worse fit than the one explicit check in main().
_COMMANDS: dict[str, Callable[[argparse.Namespace], None]] = {
    "compress": _cmd_compress,
    "signatures": _cmd_signatures,
    "dependencies": _cmd_dependencies,
    "api": _cmd_api,
    "debug": _cmd_debug,
    "collect": _cmd_collect,
    "tokens": _cmd_tokens,
    "pack": _cmd_pack,
    "tree": _cmd_tree,
    "search": _cmd_search,
    "detail": _cmd_detail,
    "freshness": _cmd_freshness,
    "skill": _cmd_skill,
    "flatten": _cmd_flatten,
    "init": _cmd_init,
    "settings": _cmd_settings,
    "doctor": _cmd_doctor,
    "index": _cmd_index,
    "deps": _cmd_deps,
    "impact": _cmd_impact,
    "path": _cmd_path,
    "hook": _cmd_hook,
    "install": _cmd_install,
}


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

    parser = _build_parser()
    argv = sys.argv[1:]
    if argv[:1] == ["dev"]:  # `ziplex dev <tool>` == the debug tool's own subcommand
        argv = argv[1:]
    args = parser.parse_args(argv)

    handler = _COMMANDS.get(args.command)
    if handler is None:
        _print_command_overview()
        return
    try:
        handler(args)
    except BrokenPipeError:  # output piped into head/less that exited early
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass


if __name__ == "__main__":
    main()
