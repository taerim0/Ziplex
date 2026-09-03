def read_text(file_path: str) -> str | None:
    """Safely reads a file as UTF-8 text.

    Returns None instead of raising on binary files, encoding issues, or
    inaccessible paths (directories, permissions, etc.). This is the shared
    entry point that keeps the pipeline from crashing on projects with
    non-text files mixed in (game assets like images, sound, etc.).
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        return None


def relative_key(file_path: str, root) -> str:
    """Uses file_path's path relative to root as a stable key (aif.json/
    checkpoint entries, search results, ...), falling back to the bare
    filename if file_path isn't actually under root.

    Using just the basename would collide for same-named files in different
    folders (e.g. multiple modules each with their own init.py, config.py),
    silently letting the last one overwrite the earlier ones. A relative-path
    key avoids that.
    """
    from pathlib import Path

    root = Path(root)
    try:
        return Path(file_path).relative_to(root).as_posix()
    except ValueError:
        return Path(file_path).name


def parent_folder(name: str) -> str:
    """A file's own immediate containing folder, POSIX-normalized -- "."
    for a root-level file (Path.parent's own natural value for a name with
    no directory component, not a special-cased empty string or "root"
    sentinel).

    Shared by every caller that groups collected files by directory
    (folder_summary.py's group_files_by_folder(), go_packages.py's
    build_go_package_index(), query_service.py's list_files() folder
    filter) so the "." root convention -- and what counts as "the same
    folder" -- can't drift between them by each re-deriving it inline.
    """
    from pathlib import Path

    return Path(name).parent.as_posix()
