import json
from pathlib import Path
from typing import Callable

from file.collector import collect_files
from file.scanner import scan_files
from file.selector import select_files, review_dangerous_files
from file.textutil import relative_key as _rel_key
from extract.code.extractor import extract_signatures, extract_dependencies, extract_api
from extract.code.compressor import compress_file
from text_references import find_text_references_for_file
from tokenizer import analyze_tokens_with_payload
from llm import analyze_rules, analyze_prompt
from freshness import build_manifest, load_previous_summaries
from confidence import estimate_confidence
from config import collection_kwargs
from tech_stack import detect_tech_stack
import checkpoint as ckpt
import summarizer

RESULT_DIR = Path(__file__).parent.parent / "result"

# The AI-guide text a structural-only pack (use_llm=False) ships in place of
# an actual analyze_prompt() result -- so a reader (human or agent) sees why
# `project.prompt` reads like this instead of assuming the pack half-failed.
STRUCTURAL_ONLY_NOTE = (
    "This is a structural-only pack (--no-llm): no AI guide or coding-rule "
    "inference was run, and each file's summary is a deterministic listing "
    "of its own extracted signatures/dependencies, not an LLM-written "
    "description of what the file does."
)


def _confirm_regenerate_failed_summaries(failed_names: list[str], interactive: bool) -> bool:
    """Asks whether to drop the cached failure placeholder
    (summarizer.SUMMARY_FAILED_PLACEHOLDER) for `failed_names` so this run
    retries them for real, instead of load_previous_summaries() silently
    carrying the same "요약 생성 실패" text forward pack after pack until
    the file's content itself happens to change.

    Same shape as checkpoint.resume_checkpoint_choice() -- a numbered
    menu, non-interactive callers get a safe default with no prompt.
    Defaults to leaving them cached (not regenerating): a repeated LLM
    failure isn't retried automatically anywhere else in the pipeline
    either (see generate_summaries()'s own docstring -- review is what's
    supposed to catch it, not a silent retry loop), and confidence.py
    already forces these to 0.0 so a human reviewing the pack sees them
    flagged regardless of which way this defaults.
    """
    if not interactive:
        return False

    print(f"\n  ⚠️  이전 pack에서 요약 생성에 실패한 파일 {len(failed_names)}개가 캐시에 있습니다:")
    for name in failed_names:
        print(f"      - {name}")
    print("  [1] 다시 생성 시도")
    print("  [2] 그대로 둠 (검토 단계에서 확인)")
    choice = input("  선택: ").strip()
    return choice == "1"


def _maybe_stop(
    check_cancelled: Callable[[], str | None] | None,
    root_path: str, root: Path, files_data: dict, rules: list | None = None, prompt: str = "",
) -> bool:
    """True if `check_cancelled` says to stop -- checkpointing first
    (reusing checkpoint.build_snapshot(), the exact shape handle_llm_failure()
    already checkpoints, so a stopped-and-saved run auto-resumes next time
    exactly like a failed one does) unless it said "discard". False (never
    stops) when check_cancelled wasn't given at all -- the CLI's synchronous
    run has no external thread to ask it to stop from in the first place;
    only a caller that runs pack() on a background thread (the GUI -- see
    gui/pack_service.py's request_cancel()) needs this, since a real Python
    thread can't be safely killed from outside once started.
    """
    if check_cancelled is None:
        return False
    action = check_cancelled()
    if action is None:
        return False
    if action == "save":
        ckpt.save_checkpoint(root_path, ckpt.build_snapshot(root, files_data, rules, prompt))
    return True


def pack(
    root_path: str,
    auto: bool = False,
    interactive: bool = True,
    use_cache: bool = True,
    use_llm: bool = True,
    preselected: list[str] | None = None,
    include: list[str] | None = None,
    ignore: list[str] | None = None,
    check_cancelled: Callable[[], str | None] | None = None,
    result_dir: str | Path | None = None,
) -> dict:
    """include/ignore are extra glob patterns (gitignore syntax -- see
    collect_files()'s own docstring for exactly how each is applied),
    unioned with whatever's already in the target project's .ziplex.json
    (config.py) rather than replacing it -- typically sourced from a CLI
    --include/--ignore flag for a one-off scope without editing the config
    file. Both apply before file selection (auto/preselected/interactive
    picker all choose from the same already-filtered candidate set), so a
    file excluded here never reaches the security scan or shows up as a
    choice at all, the same as one excluded by DEFAULT_IGNORE/.gitignore.

    auto controls file selection (skip the interactive picker, include all
    safe files); interactive controls whether a failing LLM call can prompt
    for input at all. The two are independent: `--auto` alone still lets
    checkpoint.handle_llm_failure() ask what to do on a repeated failure,
    while interactive=False makes that automatic (checkpoint + return {})
    even with file selection left interactive. `cli.py`'s `--auto-correct`
    sets both this and whether corrector.py's interactive review runs
    afterward, since both boil down to the same question: is there a
    terminal to prompt?

    preselected, when given, wins over both auto and the interactive
    select_files() picker: a list of relative names (the same keys aif.json
    ends up keyed by) to include, already decided by the caller. This is
    gui_server.py's seam -- pack_service.py's file-selection screen runs
    collect_files()/scan_files() itself to show a human the safe/dangerous
    split in the browser, then passes back what they checked, since
    select_files()'s input()-based picker has no terminal to read from over
    HTTP. An empty list is valid input (selects nothing, same as an empty
    picker response) and not the same as None (which falls through to
    auto/select_files()).

    use_cache controls incremental reuse (staleness stage 2): when a
    previous successful pack is found at the conventional RESULT_DIR path
    for this project (or result_dir, if given -- see this function's own
    note on that param, further down), any file whose content hash still
    matches gets its
    summary reused instead of spending another LLM call on it. Only summary
    is reused -- signatures/dependencies/api/compressed are always
    freshly extracted, so a human's prior manual reparenting
    (corrector.py's move_file()) never silently gets skipped along with an
    unchanged file's dependency data; a fresh pack always rebuilds
    `relationships` from scratch regardless of this flag, same as before
    incremental reuse existed. use_cache=False (CLI: `pack --no-cache`)
    also discards a leftover checkpoint from an interrupted previous run,
    not just previous summaries -- "ignore anything cached from before"
    means the checkpoint too; without this, a non-interactive caller (the
    GUI, always) would silently auto-resume that checkpoint regardless of
    use_cache, which read as a bug from the outside (a "완전히 재패킹"
    checkbox not actually starting fresh) even though each half worked as
    designed in isolation.

    use_llm=False (CLI: `pack --no-llm`) skips every LLM call entirely --
    no GEMINI_API_KEY, no network, no Gemini rate limits -- for the
    "structural-only" mode floated as a mitigation for the biggest
    non-developer onboarding barrier (getting an API key at all; see the
    `ziplex-roadmap` memory's target-audience discussion). Everything
    Tree-sitter/regex-based already runs unaffected (extraction,
    compression, tech_stack detection, the dependency graph); only the
    three LLM-touching steps change: each file's `summary` comes from
    summarizer.generate_structural_summaries() instead (a deterministic
    listing of its own signatures/dependencies -- see that function's
    docstring for exactly why this isn't pretending to be a real
    description), `rules` stays `[]` (nothing to infer without an LLM
    reading signature patterns), and `prompt` becomes STRUCTURAL_ONLY_NOTE
    instead of an AI-written guide, so a reader sees *why* those fields
    look like this rather than assuming the pack half-failed. use_cache
    still applies independently -- a file whose previous *real* summary is
    still fresh gets that reused rather than downgraded to a structural
    placeholder, since reusing a better answer already on hand is strictly
    an improvement, not a contradiction of "don't call the LLM now." One
    exception: a reused summary that's exactly summarizer's failure
    placeholder never got a real answer in the first place, so it isn't
    "reuse" so much as re-caching a miss -- see
    _confirm_regenerate_failed_summaries() below.

    check_cancelled, if given, is polled at a few natural checkpoints (once
    per file during extraction, once more right before the LLM summary
    step, and once right after it) and should return None to keep going, or
    "save"/"discard" to stop early -- see _maybe_stop()'s own docstring for
    exactly what each does. The third checkpoint exists specifically so a
    stop requested *during* the summary step (typically the longest-running
    one, and the one with no way to interrupt its own thread pool mid-
    flight) still takes effect the moment that step finishes, instead of
    sitting unconsumed for the rest of the run. Only meaningful for a caller running pack() on its own background
    thread with no other way to interrupt it (gui/pack_service.py's
    "running"-state stop buttons); a plain CLI run has no such caller and
    just leaves this unset, same as `interactive` prompts never firing for
    the GUI's own non-interactive `pack()` calls. Not instant either way --
    a network-bound LLM call already in flight when a stop is requested
    still completes before the next checkpoint is reached.

    result_dir overrides RESULT_DIR as where use_cache's incremental-reuse
    lookup (load_previous_summaries(), just below) looks for a previous
    successful pack -- not where *this* run's own aif.json ends up (that's
    always whatever save_aif() is called with separately, after pack()
    returns). Only meaningful together with a caller that also saves to a
    non-default folder: gui/pack_service.py's start_pack_job() resolves a
    project's effective output folder (settings.py's per-project pin, else
    its global default, else nothing configured) once, up front, and passes
    the exact same folder both here and to save_aif() later, so cache
    lookup and the actual save destination never drift apart. None (the
    CLI's only call shape) keeps today's behavior exactly: RESULT_DIR,
    unconditionally.
    """
    root = Path(root_path)
    effective_result_dir = Path(result_dir) if result_dir else RESULT_DIR

    # auto-detect a checkpoint -- use_cache=False means "ignore anything
    # cached from before, pack this fully fresh," which includes a leftover
    # checkpoint from an interrupted run, not just previous summaries (see
    # this function's own use_cache docstring, further up). Short-circuits
    # before ever calling resume_checkpoint_choice() in that case, so a
    # non-cached run never gets an interactive resume-or-discard prompt (or
    # a silent auto-resume, non-interactively) for something the caller
    # already said to ignore.
    checkpoint = ckpt.load_checkpoint(root_path)
    if checkpoint and (not use_cache or not ckpt.resume_checkpoint_choice(interactive)):
        checkpoint = None
        ckpt.delete_checkpoint(root_path)

    # restore from checkpoint
    restored_rules, restored_prompt, restored_files_data = ckpt.unpack_snapshot(checkpoint)

    # 1. Collect files
    print("\n📁 파일 수집 중...")
    files = collect_files(root_path, **collection_kwargs(root_path, extra_include=include, extra_ignore=ignore))

    # 2. Security scan
    print("🔒 보안 스캔 중...")
    scan_result = scan_files(files)
    safe_files = scan_result["safe"]
    dangerous = scan_result["dangerous"]
    included_anyway = []

    if dangerous:
        # Only ever asked when interactive (--auto-correct's *absence*) --
        # the same gate as every other "can we ask the terminal something"
        # decision already in this pipeline (checkpoint resume,
        # handle_llm_failure, the regenerate-cached-summary confirm),
        # independent of `auto` -- auto only changes *how* the already-safe
        # set gets selected below, not whether a human can still be asked
        # about a security decision. Non-interactive callers (--auto-correct)
        # keep today's behavior: excluded, no prompt, no way back short of
        # a second interactive run.
        included_anyway = review_dangerous_files(dangerous, root_path) if interactive else []
        if included_anyway:
            safe_files = safe_files + included_anyway

        excluded = [d["file"] for d in dangerous if d["file"] not in included_anyway]
        if excluded:
            print(f"  ⚠️  민감 파일 제외: {len(excluded)}개")
            for f in excluded:
                print(f"  ❌ {Path(f).name}")

    # 3. Select files
    if preselected is not None:
        # Trusts a name from `dangerous` too, not just safe_files: naming
        # one here already *is* the caller's explicit decision -- the GUI's
        # file-selection screen shows the same reason/matched-line detail
        # review_dangerous_files() prints above, just as a checkbox a human
        # ticks before ever calling start_pack_job(), not a prompt pack()
        # itself raises. A scripted/CI caller that builds `preselected`
        # without a human in the loop simply never names a dangerous file's
        # key in the first place.
        #
        # Excludes anything already folded into safe_files via
        # included_anyway above -- interactive=True plus a preselected list
        # both naming the same dangerous file isn't a real call site today
        # (the GUI always passes interactive=False), but without this a
        # file approved both ways would appear twice in `candidates` and
        # get selected (and summarized) twice.
        wanted = set(preselected)
        candidates = safe_files + [d["file"] for d in dangerous if d["file"] not in included_anyway]
        selected = [f for f in candidates if _rel_key(f, root) in wanted]
        print(f"  ✅ {len(selected)}개 파일 선택됨 (지정된 목록 기준)")
    elif auto:
        selected = safe_files
        print(f"  ✅ 전체 {len(selected)}개 파일 선택됨")
    else:
        selected = select_files(safe_files, root_path)

    if not selected:
        print("선택된 파일 없음.")
        return {}

    # incremental reuse (staleness stage 2): {relative key: summary} for
    # files whose content hasn't changed since the last successful pack
    previous_summaries = load_previous_summaries(root_path, selected, effective_result_dir) if use_cache else {}
    if previous_summaries:
        print(f"  ♻️  이전 pack에서 변경 없는 파일 {len(previous_summaries)}개 발견 — 요약 재사용")

        failed_previously = [
            name for name, summary in previous_summaries.items()
            if summary == summarizer.SUMMARY_FAILED_PLACEHOLDER
        ]
        if failed_previously and _confirm_regenerate_failed_summaries(failed_previously, interactive):
            for name in failed_previously:
                del previous_summaries[name]

    # 4. Tree-sitter analysis
    print("\n🔍 코드 구조 분석 중...")
    files_data = {}
    signatures_map = {}

    # All selected files' relative keys, computed once -- text_references.
    # find_text_references_for_file() needs the real candidate list to
    # match a non-code file's content against, not just file_path itself.
    all_names = [_rel_key(fp, root) for fp in selected]

    # Text-reference matches (see text_references.py), kept separate from
    # files_data[fp]["dependencies"] until *after* step 5's LLM summary
    # loop below, not merged in here -- summarizer.request_summary()/
    # analyze_batch_summaries() both switch a file's summary prompt from
    # content-based to signature/dependency-based the moment `dependencies`
    # is non-empty. Merging immediately would silently swap a text file's
    # (README, .tscn, ...) content-based prompt for a signatures=[]/
    # dependencies=[the very refs just found]-only one the instant it
    # picked up any text reference -- Gemini would summarize it having
    # never seen its actual content. Computed for every file regardless of
    # checkpoint-restore, so a run resumed mid-way still gets it.
    text_refs_by_path: dict[str, list[str]] = {}

    for file_path in selected:
        if _maybe_stop(check_cancelled, root_path, root, files_data, restored_rules, restored_prompt):
            return {}

        name = _rel_key(file_path, root)
        text_refs_by_path[file_path] = find_text_references_for_file(file_path, name, all_names)

        # restore from checkpoint
        if name in restored_files_data:
            print(f"  ✅ {name} (체크포인트에서 복원)")
            files_data[file_path] = restored_files_data[name]
            if files_data[file_path].get("signatures"):
                signatures_map[file_path] = files_data[file_path]["signatures"]
            continue

        sigs = extract_signatures(file_path)
        deps = extract_dependencies(file_path)
        apis = extract_api(file_path)
        compressed = compress_file(file_path)
        reused_summary = previous_summaries.get(name, "")

        files_data[file_path] = {
            "signatures": sigs,
            "dependencies": deps,
            "api": apis,
            "compressed": compressed,
            "summary": reused_summary
        }

        if sigs or deps:
            signatures_map[file_path] = sigs

        if reused_summary:
            print(f"  ♻️  {name} (변경 없음, 이전 요약 재사용)")
        else:
            print(f"  ✅ {name}")

    if _maybe_stop(check_cancelled, root_path, root, files_data, restored_rules, restored_prompt):
        return {}

    # 5. Summary generation -- LLM-based (the default) or, with use_llm=False,
    # a deterministic structural fallback that never touches the network at
    # all (see this function's own docstring for the full use_llm story).
    # Each LLM-generated summary is human-reviewed/corrected in
    # correct_aif() later (triaged by confidence -- see below -- so review
    # effort scales with how many summaries actually look suspicious, not
    # with project size), so this skips the retry menu: try each file once
    # in parallel (see summarizer.generate_summaries) and fill failures with
    # a placeholder (a wrong summary still gets fixed in the next step).
    pending = {
        fp: data for fp, data in files_data.items() if not data.get("summary")
    }
    if use_llm:
        print("\n🤖 LLM 분석 중...")
        for fp, summary in summarizer.generate_summaries(pending, root).items():
            files_data[fp]["summary"] = summary
    else:
        print("\n📐 구조 정보만으로 요약 생성 중 (--no-llm, LLM 미사용)...")
        for fp, summary in summarizer.generate_structural_summaries(pending, root).items():
            files_data[fp]["summary"] = summary

    # Only now -- after every summary prompt has already been built from
    # code-only `dependencies` above -- fold in the free text-reference
    # matches computed earlier. `relationships` (built later, from this
    # same `dependencies` field) is meant to include them; the LLM summary
    # step just needed to not see them yet. See text_refs_by_path's own
    # comment above for why the ordering matters.
    for fp, data in files_data.items():
        if text_refs_by_path.get(fp):
            data["dependencies"] = data["dependencies"] + text_refs_by_path[fp]

    # Confidence signal for every summary (reused, checkpoint-restored, or
    # freshly generated) -- free, no LLM call: just how much of the file's
    # own signature vocabulary shows up in its summary. correct_aif() uses
    # this to decide which summaries are worth prompting a human about.
    for data in files_data.values():
        data["confidence"] = estimate_confidence(data["summary"], data["signatures"])

    # A cancel requested *during* the summary step above (typically the
    # longest-running one) only gets checked once that step actually
    # finishes -- there's no way to interrupt generate_summaries()'s own
    # thread pool mid-flight from here. Without this checkpoint, a request
    # landing there would sit in job["cancel_action"] unconsumed until
    # pack() finishes on its own (reviewing state), never actually taking
    # effect.
    if _maybe_stop(check_cancelled, root_path, root, files_data, restored_rules, restored_prompt):
        return {}

    # extract rules (restored from checkpoint if available)
    rules = restored_rules
    if not rules and use_llm:
        print("  📋 코딩 룰 추출 중...")
        while not rules:
            rules_response = analyze_rules(signatures_map)
            try:
                rules_data = json.loads(rules_response)
                rules = rules_data.get("rules", [])
            except json.JSONDecodeError:
                rules = []

            if not rules:
                result = ckpt.handle_llm_failure(
                    "rules", "코딩 룰",
                    ckpt.build_snapshot(root, files_data),
                    root_path,
                    interactive=interactive,
                )
                if result == "EXIT":
                    return {}
                elif result is None:
                    continue
                else:
                    rules = [r.strip() for r in result.split(",")]
    elif rules:
        print("  📋 코딩 룰 (체크포인트에서 복원)")
    else:
        print("  📋 코딩 룰 추출 건너뜀 (--no-llm)")

    # generate prompt (restored from checkpoint if available)
    prompt = restored_prompt
    if not prompt and use_llm:
        print("  ✍️  AI 가이드 생성 중...")
        while not prompt:
            prompt_response = analyze_prompt(
                project_name=root.name,
                architecture=[],
                rules=rules
            )
            try:
                prompt_data = json.loads(prompt_response)
                prompt = prompt_data.get("prompt", "")
            except json.JSONDecodeError:
                prompt = ""

            if not prompt:
                result = ckpt.handle_llm_failure(
                    "prompt", "AI 가이드",
                    ckpt.build_snapshot(root, files_data, rules),
                    root_path,
                    interactive=interactive,
                )
                if result == "EXIT":
                    return {}
                elif result is None:
                    continue
                else:
                    prompt = result
    elif prompt:
        print("  ✍️  AI 가이드 (체크포인트에서 복원)")
    else:
        print("  ✍️  AI 가이드 생성 건너뜀 (--no-llm)")
        prompt = STRUCTURAL_ONLY_NOTE

    # 6. Token counting
    # Compares raw project text against the actual per-file aif.json payload
    # (just the summary -- signatures/dependencies/api/compressed are either
    # working state pruned later in correct_aif(), or split out to a sibling
    # detail.json by save_aif() and not part of what's loaded by default).
    # files_data is fully populated with summaries by now, so this reflects
    # the real savings an AI gets from reading aif.json.
    print("\n📊 토큰 분석 중...")
    token_results, _ = analyze_tokens_with_payload(selected, files_data)

    # 7. Assemble AIF.json
    aif = {
        "project": {
            "name": root.name,
            "prompt": prompt,
            # Free (no LLM call), manifest-based fact block -- see
            # tech_stack.py's own docstring for why this exists alongside
            # `rules` rather than folding into it.
            "tech_stack": detect_tech_stack(root_path),
        },
        "rules": rules,
        "tokens": {
            model: {
                "original": data["original"],
                "compressed": data["compressed"],
                "saved_pct": data["saved_pct"]
            }
            for model, data in token_results.items()
        },
        "files": {
            _rel_key(fp, root): {
                "summary": data["summary"],
                "confidence": data["confidence"],
                "signatures": data["signatures"],
                "dependencies": data["dependencies"],
                "api": data["api"],
                "compressed": data["compressed"]
            }
            for fp, data in files_data.items()
        },
        # Working state, not part of the shipped aif.json: a {file: content
        # hash} snapshot of exactly what was packed, so a later
        # freshness.check_freshness() call can tell whether this output has
        # drifted from the files on disk without re-running any of the above.
        # save_aif() pulls this out into a sibling <name>.cache.json, the
        # same way it pulls `compressed` out into <name>.detail.json.
        "_manifest": build_manifest(selected, root_path),
    }

    # delete the checkpoint on success
    ckpt.delete_checkpoint(root_path)

    return aif


def resolve_output_path(aif: dict, output_path: str | None) -> Path:
    """The path save_aif() will write aif.json to -- result/<project name>
    .json if output_path isn't given, otherwise output_path itself --
    factored out (no directory-creation or write side effects here, unlike
    save_aif() itself) so a caller that needs to know the destination
    *before* calling save_aif() can compute it identically instead of
    keeping its own copy of this fallback that could silently drift from
    save_aif()'s. gui/pack_service.py's submit_review() is exactly that
    caller: it locks this same path (see _lock_for_path()'s own comment)
    against a concurrent edit before finalizing and saving to it.
    """
    if output_path is None:
        return RESULT_DIR / f"{aif['project']['name']}.json"
    return Path(output_path)


def save_aif(aif: dict, output_path: str | None = None) -> None:
    """Writes the AIF result to output_path, or to result/<project name>.json if
    output_path isn't given (see resolve_output_path()) — mirroring
    checkpoint.CHECKPOINT_DIR, anchored to the repo root rather than the
    caller's cwd, so `pack` writes to the same place regardless of where
    it's invoked from.

    Splits two things out of `aif` into sibling files, keyed the same way as
    `files`:
    - `compressed` -> "<name>.detail.json". aif.json (summary + relationships)
      is what an AI reads by default -- for a file with no compressed body to
      strip (README, config, lang files, ...) `compressed` is close to the raw
      original, so shipping it unconditionally on every file undoes the token
      savings for exactly the files that benefit least from it. mcp_server.py's
      get_detail tool is what actually fetches detail.json today, only for
      files an AI decides it needs.
    - `_manifest` -> "<name>.cache.json" (flat, not per-file). A {file: content
      hash} snapshot of what was packed, for freshness.check_freshness() to
      compare against later -- not part of aif.json itself, since it's
      packaging-internal bookkeeping an AI reading the project has no use for.
    """
    output_path = resolve_output_path(aif, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = aif.get("_manifest")

    lean_files = {}
    detail = {}
    for name, data in aif["files"].items():
        lean_files[name] = {k: v for k, v in data.items() if k != "compressed"}
        detail[name] = {"compressed": data.get("compressed", "")}

    lean_aif = {k: v for k, v in aif.items() if k != "_manifest"}
    lean_aif["files"] = lean_files

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(lean_aif, f, ensure_ascii=False, indent=2)
    print(f"\n✅ AIF.json 저장됨: {output_path}")

    detail_path = output_path.with_name(f"{output_path.stem}.detail.json")
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False, indent=2)
    print(f"📦 상세 정보(compressed) 저장됨: {detail_path}")

    if manifest is not None:
        cache_path = output_path.with_name(f"{output_path.stem}.cache.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print(f"🗂️  캐시(해시) 저장됨: {cache_path}")
