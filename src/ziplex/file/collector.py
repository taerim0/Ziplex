import os
from pathlib import Path
import pathspec

from ..file.textutil import read_text
from .media import is_media_file

# Common reproducible build outputs / dependency & tool caches, across
# ecosystems beyond the JS/Python ones already listed above. Not exhaustive
# (no fixed list can be) -- the content-based binary filter in collect_files()
# below is the general safety net; this just pre-skips whole directories that
# would otherwise get walked and read one file at a time for no reason.
DEFAULT_IGNORE = [
    "node_modules/",
    ".git/",
    "__pycache__/",
    "*.pyc",
    "dist/",
    "build/",
    "*.log",
    "*.lock",
    ".env",
    "venv/",
    ".venv/",

    ".gradle/",
    ".mvn/",
    "target/",
    ".next/",
    ".nuxt/",
    ".cache/",
    ".parcel-cache/",
    ".turbo/",

    ".pytest_cache/",
    ".mypy_cache/",
    ".tox/",
    ".ruff_cache/",
    "coverage/",
    ".nyc_output/",

    ".terraform/",
    ".DS_Store",
    "Thumbs.db",
]

def collect_files(root_path: str, include: list[str] | None = None, ignore: list[str] | None = None) -> list[str]:
    """include/ignore are optional extra glob patterns (gitignore syntax --
    same pathspec engine DEFAULT_IGNORE/.gitignore already use, so "**"
    works correctly), typically sourced from a project's .ziplex.json (see
    config.py) or a CLI --include/--ignore flag, layered on top of -- never
    replacing -- DEFAULT_IGNORE and the project's own .gitignore.

    ignore is merged into the same pathspec used for directory pruning
    during the walk, so an ignored directory is skipped the same efficient
    way a DEFAULT_IGNORE one already is. include is applied as a pure
    post-filter on the final file list instead: a file must match at least
    one include pattern to survive when any are given (no include patterns
    at all keeps today's "everything not ignored" behavior) -- deliberately
    not used to prune directories during the walk, since an include pattern
    like "src/**/*.py" says nothing about whether an unrelated-looking
    intermediate directory might still contain a matching file deeper in.
    """
    root = Path(root_path)

    ignore_patterns = DEFAULT_IGNORE.copy()
    gitignore_path = root / ".gitignore"
    if gitignore_path.exists():
        # read_text() (not a raw open()) so a non-UTF-8 .gitignore -- a
        # stray CP1252 byte in a comment is plausible for a legacy repo --
        # is skipped instead of crashing every CLI command that goes
        # through collect_files() first.
        gitignore_content = read_text(str(gitignore_path))
        if gitignore_content is not None:
            gitignore_lines = [
                line.strip()
                for line in gitignore_content.splitlines()
                if line.strip() and not line.startswith("#")
            ]
            ignore_patterns.extend(gitignore_lines)
    if ignore:
        ignore_patterns.extend(ignore)

    spec = pathspec.PathSpec.from_lines("gitignore", ignore_patterns)
    include_spec = pathspec.PathSpec.from_lines("gitignore", include) if include else None

    collected = []
    for dirpath, dirnames, filenames in os.walk(root):
        # skip walking into excluded directories entirely
        dirnames[:] = [
            d for d in dirnames
            if not spec.match_file(
                str(Path(dirpath).relative_to(root) / d) + "/"
            )
        ]

        for filename in filenames:
            file_path = Path(dirpath) / filename
            relative = file_path.relative_to(root)
            if spec.match_file(str(relative)):
                continue
            if include_spec is not None and not include_spec.match_file(str(relative)):
                continue

            # No name-pattern list can enumerate every binary format a project
            # might contain (images, fonts, compiled artifacts, checksums...).
            # Detect it directly instead: a file unreadable as text has nothing
            # for the LLM to summarize, and letting it through just means a
            # summary hallucinated from the filename alone, wasted tokens in
            # every downstream step, and a wasted LLM call. One deliberate
            # exception: a *recognized* media asset (image/video/audio/font,
            # see file/media.py) survives too, despite also being binary --
            # packager.py gives it a free, metadata-only summary instead of an
            # LLM one, so letting it through costs nothing, and keeping it out
            # entirely used to make it invisible everywhere downstream (never
            # in `files`, never resolvable as another file's dependency
            # target -- a Godot scene's ext_resource, an <img src>, a README
            # naming a screenshot). is_media_file() checked first, not
            # read_text() -- both orders are logically equivalent (this is
            # an OR: keep if text OR media-extension), but reading a whole
            # file into memory just to attempt a doomed UTF-8 decode is real,
            # avoidable I/O for a large binary (a video/audio asset
            # especially) that a plain extension check already answers for
            # free; only a file collect_files() doesn't already recognize as
            # media needs the real read_text() check at all.
            if is_media_file(str(file_path)) is None and read_text(str(file_path)) is None:
                continue

            collected.append(str(file_path))

    return sorted(collected)


def print_tree(files: list[str], root_path: str):
    root = Path(root_path)
    # .resolve() just for display -- Path(".").name is "" (no name component
    # at all), which used to print a bare "\n/" instead of the actual folder
    # name when root_path is ".". root itself stays unresolved below, to
    # match the also-unresolved paths in `files`.
    print(f"\n{root.resolve().name}/")
    for file_path in files:
        relative = Path(file_path).relative_to(root)
        depth = len(relative.parts) - 1
        indent = "  " * depth
        print(f"{indent}├── {relative.name}")