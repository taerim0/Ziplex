import contextvars
import os
import functools
import threading

# Within one pack() every stage (collection, hashing, extraction, text refs,
# token counts) used to re-read and re-decode the same files 6-7 times.
# read_cache() memoizes read_text() for the duration of a call, bounded by a
# character budget; outside it read_text() always hits the disk.
_cache: contextvars.ContextVar["_ReadCache | None"] = contextvars.ContextVar("ziplex_read_cache", default=None)

READ_CACHE_MAX_CHARS = 256 * 1024 * 1024


class _ReadCache:
    def __init__(self, max_chars: int):
        self.entries: dict[str, str | None] = {}
        self.chars = 0
        self.max_chars = max_chars
        self.lock = threading.Lock()


def cached_reads(fn):
    """Runs `fn` with read_text() memoized (see the comment above _cache).
    Nested use shares the outer cache. Worker threads see it only when
    submitted through contextvars.copy_context().run."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if _cache.get() is not None:
            return fn(*args, **kwargs)
        token = _cache.set(_ReadCache(READ_CACHE_MAX_CHARS))
        try:
            return fn(*args, **kwargs)
        finally:
            _cache.reset(token)
    return wrapper


def read_text(file_path: str) -> str | None:
    """Safely reads a file as UTF-8 text.

    Returns None instead of raising on binary files, encoding issues, or
    inaccessible paths (directories, permissions, etc.). This is the shared
    entry point that keeps the pipeline from crashing on projects with
    non-text files mixed in (game assets like images, sound, etc.).
    """
    cache = _cache.get()
    if cache is not None:
        key = str(file_path)
        with cache.lock:
            if key in cache.entries:
                return cache.entries[key]
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
    except (UnicodeDecodeError, OSError):
        text = None
    if cache is not None:
        size = len(text) if text else 0
        with cache.lock:
            if cache.chars + size <= cache.max_chars:
                cache.entries[key] = text
                cache.chars += size
    return text


def relative_key(file_path: str, root) -> str:
    """Uses file_path's path relative to root as a stable key (aif.json/
    checkpoint entries, search results, ...), falling back to the bare
    filename if file_path isn't actually under root.

    Using just the basename would collide for same-named files in different
    folders (e.g. multiple modules each with their own init.py, config.py),
    silently letting the last one overwrite the earlier ones. A relative-path
    key avoids that.
    """
    # String fast path for the common case (an already-normalized path under
    # root); anything unusual falls through to pathlib's lexical semantics.
    # A backslash is a separator only where the OS says so (a legal filename
    # character on POSIX).
    root_str = str(root).replace(os.sep, "/").rstrip("/")
    path_str = str(file_path).replace(os.sep, "/")
    if root_str and path_str.startswith(root_str + "/") and "/./" not in path_str and "//" not in path_str[len(root_str):]:
        return path_str[len(root_str) + 1:]

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
    relationship editor) can't drift into normalizing differently. A
    leading "./" is stripped too: `./src/a.py` names the same key, and a
    miss there is either a spurious not-found or, for the filters that skip
    unknown names by design, a silently empty answer.
    """
    value = value.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


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
