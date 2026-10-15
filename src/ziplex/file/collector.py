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
    # Lockfiles and minified/source-map build output: large, generated, and
    # summarize to nothing useful. Manifests (package.json, pnpm-workspace.yaml,
    # go.mod, ...) stay collected. "*.lock" covers yarn/Cargo/poetry/Gemfile/
    # composer/Pipfile/flake lockfiles.
    "*.lock",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "bun.lockb",
    "go.sum",
    "*.min.js",
    "*.min.css",
    "*.js.map",
    "*.mjs.map",
    "*.cjs.map",
    "*.css.map",
    "*.d.ts.map",
    ".env",
    # .env.local/.env.production/... carry the same real secrets .env does
    # and used to be collected (and scan as safe more often than not); the
    # conventional committed templates stay in, since they document which
    # settings exist and hold placeholders, not values.
    ".env.*",
    "!.env.example",
    "!.env.sample",
    "!.env.template",
    "!.env.dist",
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

    # Ziplex's own default output location (packager.DEFAULT_OUTPUT_SUBDIR)
    # -- a real, confirmed bug found live once the default moved from an
    # install-relative RESULT_DIR to project-relative: without this, a
    # second `pack` on the same project would collect its own previous
    # aif.json/detail.json/cache.json as *input* files (they sit inside the
    # very tree being walked), getting summarized, hashed into the next
    # manifest, and even flipping one file's summary to a cached failure
    # placeholder purely from packing itself. `.ziplex.json` (config.py's
    # per-project config, a single file, always meant to be read/committed)
    # is a completely different path from this `.ziplex/` directory and
    # untouched by this pattern.
    ".ziplex/",
]

def collect_files(root_path: str, include: list[str] | None = None, ignore: list[str] | None = None) -> list[str]:
    """Every file under root_path that survives walk_candidates()'s pattern
    filters and is either decodable text or a recognized media asset."""
    return [
        fp for fp in walk_candidates(root_path, include, ignore)
        if is_media_file(fp) is not None or read_text(fp) is not None
    ]


def walk_candidates(root_path: str, include: list[str] | None = None, ignore: list[str] | None = None) -> list[str]:
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

    # (folder relative to root, its own .gitignore's spec) for every
    # .gitignore below the root -- git applies each one to paths under its
    # own folder, relative to that folder. Only the root one used to be
    # read, so a file listed in svc/.gitignore was still collected.
    nested_specs: list[tuple[str, pathspec.PathSpec]] = []

    def is_ignored(relative_posix: str) -> bool:
        if spec.match_file(relative_posix):
            return True
        for folder, folder_spec in nested_specs:
            if relative_posix.startswith(folder + "/") and folder_spec.match_file(relative_posix[len(folder) + 1:]):
                return True
        return False

    collected = []
    for dirpath, dirnames, filenames in os.walk(root):
        # String paths per directory, not pathlib per file (pathlib parsing
        # was most of the walk's cost on large trees).
        dir_obj = Path(dirpath)
        rel_dir = dir_obj.relative_to(root).as_posix()
        rel_prefix = "" if rel_dir == "." else rel_dir + "/"
        if rel_dir != "." and ".gitignore" in filenames:
            nested_content = read_text(str(Path(dirpath) / ".gitignore"))
            if nested_content is not None:
                lines = [l.strip() for l in nested_content.splitlines() if l.strip() and not l.startswith("#")]
                if lines:
                    nested_specs.append((rel_dir, pathspec.PathSpec.from_lines("gitignore", lines)))

        # skip walking into excluded directories entirely
        dirnames[:] = [
            d for d in dirnames
            if not is_ignored(rel_prefix + d + "/")
        ]

        for filename in filenames:
            relative_posix = rel_prefix + filename
            if is_ignored(relative_posix):
                continue
            if include_spec is not None and not include_spec.match_file(relative_posix):
                continue

            file_path = str(dir_obj / filename)
            collected.append(file_path)

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