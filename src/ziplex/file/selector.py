from pathlib import Path


def review_dangerous_files(dangerous: list[dict], root_path: str) -> list[str]:
    """Interactive review for files scan_files() flagged as sensitive --
    shows *why* each one was flagged (the matched line, not the whole file:
    enough to judge a false positive without a human going to open the
    file themselves -- see scan_file()'s docstring) and lets specific ones
    be included anyway, instead of today's silent, unconditional exclusion
    with no way back. Returns the raw paths (dangerous[i]["file"]) chosen
    to include; pack() folds them into `safe_files` so every selection mode
    downstream (--auto, the interactive picker, preselected) treats them
    exactly like any other safe file from here on.

    Only ever called when interactive (see pack()'s own comment at the call
    site) -- same "--auto-correct's absence, not --auto's" gate as every
    other place this pipeline can ask the terminal something.
    """
    print(f"\n⚠️  민감 파일 {len(dangerous)}개 감지됨:\n")
    for i, entry in enumerate(dangerous, 1):
        relative = Path(entry["file"]).relative_to(root_path)
        print(f"  [{i}] {relative}")
        print(f"      {entry.get('reason') or '민감 정보로 추정됨'}")
        if entry.get("line") and entry.get("matched_text") is not None:
            print(f"      {entry['line']}번째 줄: {entry['matched_text'].strip()}")

    print("\n그래도 포함할 파일 번호 (쉼표로 구분 / 없으면 엔터): ", end="")
    user_input = input().strip()
    if not user_input:
        return []

    try:
        indices = [int(x.strip()) for x in user_input.split(",")]
    except ValueError:
        print("잘못된 입력입니다. 전부 제외합니다.")
        return []

    included = []
    for idx in indices:
        if 1 <= idx <= len(dangerous):
            included.append(dangerous[idx - 1]["file"])
        else:
            print(f"  ⚠️  [{idx}] 범위 초과 → 무시")

    if included:
        print(f"\n✅ {len(included)}개 그래도 포함됨")
    return included


def display_files(files: list[str], root_path: str) -> None:
    print(f"\n📁 수집된 파일 ({len(files)}개)\n")
    for i, file_path in enumerate(files, 1):
        relative = Path(file_path).relative_to(root_path)
        print(f"  [{i}] {relative}")


def select_files(files: list[str], root_path: str) -> list[str]:
    display_files(files, root_path)

    print("\n선택 (쉼표로 구분 / 전체=a / 취소=q): ", end="")
    user_input = input().strip()

    # cancel
    if user_input.lower() == "q":
        print("취소됨.")
        return []

    # select all
    if user_input.lower() == "a":
        print(f"\n✅ 전체 {len(files)}개 선택됨")
        return files

    # select by number
    try:
        indices = [int(x.strip()) for x in user_input.split(",")]
        selected = []
        for idx in indices:
            if 1 <= idx <= len(files):
                selected.append(files[idx - 1])
            else:
                print(f"  ⚠️  [{idx}] 범위 초과 → 무시")

        print(f"\n✅ {len(selected)}개 선택됨")
        for f in selected:
            relative = Path(f).relative_to(root_path)
            print(f"  {relative}")

        return selected

    except ValueError:
        print("잘못된 입력입니다.")
        return []