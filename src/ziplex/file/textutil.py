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


def human_size(size_bytes: int) -> str:
    """B/KB/MB/GB with one decimal place above B -- shared so a byte count
    reads the same way everywhere one's shown (media.py's media_summary(),
    checkpoint.list_checkpoints()'s cli.py renderer) rather than each call
    site inventing its own rounding/unit convention.
    """
    size = float(size_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def normalize_path(value: str) -> str:
    """Every key the pipeline compares a caller-supplied path against
    (`files`/`relationships`) is always POSIX-style (`/`) by the time it
    reaches aif.json, but nothing on the *reading/editing* side enforced
    that on a caller-supplied path argument -- a backslash path (natural to
    type on Windows, a real risk since this project is developed on
    Windows) used to silently fail to match any real key instead of being
    normalized first. Shared so query_service.py's read-side lookups and
    file/relationship.py's add_relationship()/remove_relationship() (the
    write-side API behind `ziplex link`/`ziplex unlink` and the GUI's
    relationship editor) can't drift into normalizing differently.
    """
    return value.replace("\\", "/")


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
