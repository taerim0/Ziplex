from file.relationship import build_stem_map, resolve_dependency, build_tree


def correct_relationships(aif: dict) -> dict:
    print(f"\n🔗 파일 간 관계 수정")
    print("(완료=q, 엔터=유지)\n")

    files = list(aif["files"].keys())
    stem_map = build_stem_map(files)

    def resolve(dep: str) -> str | None:
        """dep(import 경로 또는 이미 확정된 파일명)이 프로젝트 내부 파일이면 파일명을, 아니면 None."""
        matched = resolve_dependency(dep, stem_map)
        return matched if matched in files else None

    def has_cycle(from_file: str, to_file: str) -> bool:
        """to_file이 from_file의 조상인지 확인 (순환 참조 감지)"""
        visited = set()
        queue = [to_file]
        while queue:
            current = queue.pop()
            if current == from_file:
                return True
            if current in visited:
                continue
            visited.add(current)
            deps = aif["files"].get(current, {}).get("dependencies", [])
            for dep in deps:
                matched = resolve(dep)
                if matched:
                    queue.append(matched)
        return False

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

    # 여러 번 수정 가능하게 루프
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

            move_file = files[move_idx]

            print(f"  [{move_file}] 을 어느 파일의 자식으로?")
            print("  부모 파일 번호 (취소=q): ", end="")
            parent_input = input().strip()

            if parent_input.lower() == "q" or not parent_input:
                continue

            parent_idx = int(parent_input) - 1
            if parent_idx < 0 or parent_idx >= len(files):
                print("  ⚠️  범위 초과")
                continue

            parent_file = files[parent_idx]

            if move_file == parent_file:
                print("  ⚠️  같은 파일 선택 불가")
                continue

            # 순환 참조 감지
            if has_cycle(move_file, parent_file):
                print(f"  ⚠️  순환 참조 발생 → 이동 불가")
                continue

            # 기존 부모에서 제거 (원본이 import 경로든 이전에 옮겨 붙인 파일명이든 매칭)
            for name in files:
                deps = aif["files"][name].get("dependencies", [])
                aif["files"][name]["dependencies"] = [
                    d for d in deps if resolve(d) != move_file
                ]

            # 새 부모의 자식으로 추가
            if "dependencies" not in aif["files"][parent_file]:
                aif["files"][parent_file]["dependencies"] = []
            aif["files"][parent_file]["dependencies"].append(move_file)

            print(f"  ✅ {move_file} → {parent_file} 자식으로 이동\n")

        except (ValueError, IndexError):
            print("  ⚠️  잘못된 입력")

    return aif

def correct_aif(aif: dict) -> dict:
    print("\n" + "=" * 50)
    print("✏️  사용자 보정 (엔터 = 유지, 내용 입력 = 수정)")
    print("=" * 50)

    # 1. 프로젝트 이름 수정
    current_name = aif["project"]["name"]
    print(f"\n📌 프로젝트 이름: {current_name}")
    new_name = input("  수정 (엔터=유지): ").strip()
    if new_name:
        aif["project"]["name"] = new_name

    # 2. AI 가이드 수정
    current_prompt = aif["project"]["prompt"]
    print(f"\n✍️  AI 가이드:\n  {current_prompt}")
    new_prompt = input("  수정 (엔터=유지): ").strip()
    if new_prompt:
        aif["project"]["prompt"] = new_prompt

    # 3. 코딩 룰 수정
    print(f"\n📋 코딩 룰:")
    for i, rule in enumerate(aif["rules"], 1):
        print(f"  [{i}] {rule}")

    print("\n  룰 추가 (a), 삭제 (d번호), 유지 (엔터): ", end="")
    rule_input = input().strip()

    if rule_input.lower() == "a":
        new_rule = input("  추가할 룰 입력: ").strip()
        if new_rule:
            aif["rules"].append(new_rule)
            print(f"  ✅ 추가됨: {new_rule}")

    elif rule_input.lower().startswith("d"):
        try:
            idx = int(rule_input[1:]) - 1
            if 0 <= idx < len(aif["rules"]):
                removed = aif["rules"].pop(idx)
                print(f"  ✅ 삭제됨: {removed}")
        except (ValueError, IndexError):
            print("  ⚠️  잘못된 입력")

    # 4. 파일별 summary 수정
    print(f"\n📄 파일별 Summary:")
    for file_name, data in aif["files"].items():
        print(f"\n  {file_name}: {data['summary']}")
        new_summary = input("  수정 (엔터=유지): ").strip()
        if new_summary:
            aif["files"][file_name]["summary"] = new_summary

    aif = correct_relationships(aif)

    # 보정 완료 후 relationships 생성
    aif["relationships"] = build_tree(aif["files"])

    print("\n✅ 보정 완료")
    return aif