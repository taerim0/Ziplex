import argparse
import json
import sys
from pathlib import Path

from .extract.code.extractor import extract_signatures, extract_dependencies, extract_api, debug_tree
from .extract.code.compressor import compress_file
from .file.collector import collect_files, print_tree as print_file_tree
from .file.scanner import scan_files
from .file.media import classify_media_file, media_summary
from .file.textutil import relative_key as _rel_key
from .text_references import find_text_references_for_file
from .tokenizer import analyze_tokens, analyze_tokens_with_compression
from .file.selector import select_files, review_dangerous_files
from .llm import analyze_rules, analyze_prompt, LANGUAGE_NAMES
from .summarizer import generate_summaries
from .packager import pack, save_aif
from .corrector import correct_aif
from .edits import finalize_aif
from .file.relationship import build_tree, print_tree as print_dependency_tree
from .search import search_files, read_detail_range
from .freshness import check_freshness
from .skill_export import export_skill
from .config import init_config, CONFIG_FILENAME, collection_kwargs as _collection_kwargs, collect_and_scan as _collect_and_scan


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

    sel = sub.add_parser("select", help="파일 선택")
    sel.add_argument("path", help="프로젝트 폴더 경로")

    an = sub.add_parser("analyze", help="LLM 분석")
    an.add_argument("path", help="프로젝트 폴더 경로")
    an.add_argument("--lang", choices=list(LANGUAGE_NAMES), default="en",
                     help="요약/코딩 룰/AI 가이드의 언어 (기본값 및 권장값: en)")

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

    elif args.command == "select":
        scan_result = _collect_and_scan(args.path)
        safe_files = scan_result["safe"]
        dangerous = scan_result["dangerous"]

        if dangerous:
            # always interactive (this whole subcommand is the interactive
            # picker), so always offered the chance to include one anyway --
            # same review file/selector.review_dangerous_files() gives
            # pack()'s own picker path.
            included_anyway = review_dangerous_files(dangerous, args.path)
            safe_files = safe_files + included_anyway
            excluded = [d["file"] for d in dangerous if d["file"] not in included_anyway]
            if excluded:
                print(f"\n⚠️  민감 파일 제외됨: {len(excluded)}개")
                for f in excluded:
                    print(f"  ❌ {f}")

        selected = select_files(safe_files, args.path)

    elif args.command == "analyze":
        # 1. Collect files
        safe_files = _collect_and_scan(args.path)["safe"]

        print(f"\n📁 분석 대상: {len(safe_files)}개 파일\n")

        # 2. Per-file analysis -- delegates to summarizer.generate_summaries(),
        # the same batched/threaded/retry-once-then-placeholder path pack()
        # itself uses for its own per-file summaries, instead of a bespoke
        # one-call-per-file loop with its own separate failure placeholder
        # ("분석 실패" vs summarizer.SUMMARY_FAILED_PLACEHOLDERS). A future fix to
        # batching/retry/placeholder handling there (see CLAUDE.md's
        # `summarizer.py` bullet) now applies here too, instead of silently
        # missing this command the way a hand-rolled duplicate would.
        pending = {}
        signatures_map = {}
        media_summaries = {}
        for file in safe_files:
            media_kind = classify_media_file(file)
            if media_kind:
                # Same free, no-LLM metadata summary pack() gives a media
                # asset (see file/media.py) -- without this branch it fell
                # straight into extract_signatures/extract_dependencies
                # (both [] for a media file) and the "no signatures, no
                # dependencies" skip just below, silently vanishing from
                # this command's own output despite being counted in the
                # "분석 대상" total printed above.
                media_summaries[file] = media_summary(file, media_kind)
                continue

            sigs = extract_signatures(file)
            deps = extract_dependencies(file)

            if not sigs and not deps:
                continue

            pending[file] = {"signatures": sigs, "dependencies": deps}
            signatures_map[file] = sigs

        print(f"  🔍 {len(pending)}개 파일 분석 중...")
        summaries = generate_summaries(pending, Path(args.path), lang=args.lang)
        summaries.update(media_summaries)

        # 3. Extract rules
        print(f"\n  📋 코딩 룰 추출 중...")
        rules_response = analyze_rules(signatures_map, lang=args.lang)
        try:
            rules_data = json.loads(rules_response)
        except json.JSONDecodeError:
            rules_data = {"rules": []}

        # 4. Generate prompt
        print(f"  ✍️  AI 가이드 생성 중...\n")
        prompt_response = analyze_prompt(
            project_name=Path(args.path).name,
            architecture=[],
            rules=rules_data["rules"],
            lang=args.lang,
        )
        try:
            prompt_data = json.loads(prompt_response)
        except json.JSONDecodeError:
            prompt_data = {"prompt": "생성 실패"}

        # 5. Print results
        print("=" * 50)
        print("📄 파일별 Summary")
        print("=" * 50)
        for file, summary in summaries.items():
            print(f"  {Path(file).name}: {summary}")

        print("\n" + "=" * 50)
        print("📋 코딩 룰")
        print("=" * 50)
        for rule in rules_data["rules"]:
            print(f"  - {rule}")

        print("\n" + "=" * 50)
        print("✍️  AI 가이드")
        print("=" * 50)
        print(f"  {prompt_data['prompt']}")

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
        files_data = {}
        for file_path in safe_files:
            name = _rel_key(file_path, args.path)
            deps = extract_dependencies(file_path) + find_text_references_for_file(file_path, name, all_names)
            files_data[name] = {"dependencies": deps}

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

        safe_files = _collect_and_scan(args.path)["safe"]
        report = check_freshness(safe_files, args.path, manifest)

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

if __name__ == "__main__":
    main()