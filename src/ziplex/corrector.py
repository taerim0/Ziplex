"""Interactive terminal flow for reviewing/editing an `aif` dict before it ships.

This module owns all the input()/print() calls; every actual edit is
delegated to a pure function in edits.py or file/relationship.py. That split
means the same edits are reachable from something other than a terminal (a
future MCP server or GUI backend) without touching this file -- see edits.py
and file/relationship.py's move_file()/build_tree() for the reusable part.
`pack --auto-correct` skips this module entirely and calls
edits.finalize_aif() directly instead.
"""

from .edits import (
    set_project_name,
    set_project_prompt,
    add_rule,
    remove_rule,
    set_file_summary,
    set_folder_summary,
    finalize_aif,
)
from .file.relationship import build_stem_map, resolve_dependency, move_file, CycleError
from .confidence import triage


def correct_relationships(aif: dict) -> dict:
    print(f"\n🔗 파일 간 관계 수정")
    print("(완료=q, 엔터=유지)")
    # A real, reported pain point: this loop reparents one file at a time
    # by typed index, with no search -- workable for a quick fix or two,
    # but doesn't scale to a real project's file count. `ziplex-gui`'s
    # relationship editor (search + a mini dependency graph) or the
    # one-shot `ziplex link`/`ziplex unlink <aif_path> <file> <target>`
    # CLI commands (cli.py -- edit an already-*saved* aif.json directly,
    # no interactive loop at all) are both better fits for anything past a
    # couple of edits; this loop stays as the fallback for a GUI-less
    # environment (SSH-only, no browser).
    print("(대규모로 고칠 거면 ziplex-gui 또는 pack 이후 `ziplex link`/`ziplex unlink` 추천)\n")

    files = list(aif["files"].keys())
    stem_map = build_stem_map(files)

    def resolve(dep: str) -> str | None:
        return resolve_dependency(dep, stem_map)

    def print_current_tree():
        all_children = set()
        for name in files:
            deps = aif["files"][name].get("dependencies", [])
            for dep in deps:
                matched = resolve(dep)
                if matched:
                    all_children.add(matched)

        def print_node(name, ancestors, depth=1):
            indent = "  " * depth
            deps = aif["files"].get(name, {}).get("dependencies", [])
            for dep in deps:
                matched = resolve(dep)
                if matched:
                    if matched in ancestors:
                        print(f"{indent}   └── 📄 {matched} (순환 참조 → 생략)")
                        continue
                    print(f"{indent}   └── 📄 {matched}")
                    print_node(matched, ancestors | {matched}, depth + 1)
                else:
                    print(f"{indent}   └── 📦 {dep}")

        for i, name in enumerate(files, 1):
            if name in all_children:
                continue
            print(f"  [{i}] {name}")
            print_node(name, {name})

    # loop so the user can make multiple moves
    while True:
        print_current_tree()
        print("\n  이동할 파일 번호 (완료=q): ", end="")
        move_input = input().strip()

        if move_input.lower() == "q" or not move_input:
            break

        try:
            move_idx = int(move_input) - 1
            if move_idx < 0 or move_idx >= len(files):
                print("  ⚠️  범위 초과")
                continue

            move_file_name = files[move_idx]

            print(f"  [{move_file_name}] 을 어느 파일의 자식으로?")
            print("  부모 파일 번호 (취소=q): ", end="")
            parent_input = input().strip()

            if parent_input.lower() == "q" or not parent_input:
                continue

            parent_idx = int(parent_input) - 1
            if parent_idx < 0 or parent_idx >= len(files):
                print("  ⚠️  범위 초과")
                continue

            parent_file = files[parent_idx]

            if move_file_name == parent_file:
                print("  ⚠️  같은 파일 선택 불가")
                continue

            try:
                move_file(aif["files"], move_file_name, parent_file)
            except CycleError:
                print(f"  ⚠️  순환 참조 발생 → 이동 불가")
                continue

            print(f"  ✅ {move_file_name} → {parent_file} 자식으로 이동\n")

        except (ValueError, IndexError):
            print("  ⚠️  잘못된 입력")

    return aif


def correct_aif(aif: dict) -> dict:
    print("\n" + "=" * 50)
    print("✏️  사용자 보정 (엔터 = 유지, 내용 입력 = 수정)")
    print("=" * 50)

    # 1. Correct project name
    print(f"\n📌 프로젝트 이름: {aif['project']['name']}")
    new_name = input("  수정 (엔터=유지): ").strip()
    if new_name:
        set_project_name(aif, new_name)

    # 2. Correct AI guide
    print(f"\n✍️  AI 가이드:\n  {aif['project']['prompt']}")
    new_prompt = input("  수정 (엔터=유지): ").strip()
    if new_prompt:
        set_project_prompt(aif, new_prompt)

    # 3. Correct coding rules
    print(f"\n📋 코딩 룰:")
    for i, rule in enumerate(aif["rules"], 1):
        print(f"  [{i}] {rule}")

    print("\n  룰 추가 (a), 삭제 (d번호), 유지 (엔터): ", end="")
    rule_input = input().strip()

    if rule_input.lower() == "a":
        new_rule = input("  추가할 룰 입력: ").strip()
        if new_rule:
            add_rule(aif, new_rule)
            print(f"  ✅ 추가됨: {new_rule}")

    elif rule_input.lower().startswith("d"):
        try:
            idx = int(rule_input[1:]) - 1
            if 0 <= idx < len(aif["rules"]):
                removed = aif["rules"][idx]
                remove_rule(aif, idx)
                print(f"  ✅ 삭제됨: {removed}")
        except (ValueError, IndexError):
            print("  ⚠️  잘못된 입력")

    # 4. Correct per-file summaries -- triaged by confidence (confidence.py):
    # only summaries flagged low-confidence (their wording doesn't overlap
    # with the file's own extracted signatures) get prompted. Reviewing
    # every single file doesn't scale past a handful of them, so the rest
    # are auto-kept -- still editable by hand in aif.json afterward.
    print(f"\n📄 파일별 Summary:")
    needs_review, auto_kept = triage(aif["files"])

    for file_name in needs_review:
        data = aif["files"][file_name]
        print(f"\n  ⚠️  {file_name} (신뢰도 {data.get('confidence', 1.0)}): {data['summary']}")
        # Show what actually triggered the low score -- confidence.py flagged
        # this because the summary's wording doesn't overlap with these, so
        # printing them lets a human judge the mismatch right here instead of
        # having to go open the file themselves (the flagged subset always
        # has signatures: estimate_confidence() returns 1.0, never below
        # REVIEW_THRESHOLD, for a file with none -- text-only files never
        # reach this branch).
        signatures = data.get("signatures", [])
        if signatures:
            print("     실제 시그니처:")
            shown = signatures[:10]
            for sig in shown:
                print(f"       - {sig}")
            if len(signatures) > len(shown):
                print(f"       ... 외 {len(signatures) - len(shown)}개")
        new_summary = input("  수정 (엔터=유지): ").strip()
        if new_summary:
            set_file_summary(aif, file_name, new_summary)

    if auto_kept:
        print(f"\n  ✅ 신뢰도 높은 요약 {len(auto_kept)}개는 자동 유지됩니다 (필요하면 aif.json에서 직접 수정 가능):")
        for file_name in auto_kept:
            print(f"     {file_name}")

    # 5. Correct per-folder summaries -- lighter-weight than per-file
    # summaries above: folder_summary.py has no confidence signal of its
    # own, so every folder is shown (not just low-confidence ones), but a
    # project's folder count is always far smaller than its file count.
    folders = aif.get("folders", {})
    if folders:
        print(f"\n🗂️  폴더 Summary:")
        for folder_path, data in folders.items():
            display = folder_path if folder_path != "." else "(최상위)"
            print(f"\n  📁 {display}: {data.get('summary', '')}")
            new_summary = input("  수정 (엔터=유지): ").strip()
            if new_summary:
                set_folder_summary(aif, folder_path, new_summary)

    aif = correct_relationships(aif)
    aif = finalize_aif(aif)

    print("\n✅ 보정 완료")
    return aif
