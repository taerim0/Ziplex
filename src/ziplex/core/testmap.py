"""Which indexed files are tests, which non-import edges tie a test to code
(pytest conftest.py, Go same-package files), and which changed files make a
targeted test selection unsafe (build/config/lockfiles)."""
import posixpath
import re

# Folder names that mark tests for languages whose runners discover by folder
# (rspec, JUnit's src/test, ...). Python and JS/TS runners discover by file
# name, so a helper under tests/ (or Django's django/test/ package) isn't one.
_TEST_DIRS = {"tests", "test", "spec", "specs", "testing"}
_NAME_ONLY_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".go"}
_TEST_NAME = re.compile(
    r"(^test_.*\.py$|.*_test\.py$|^tests?\.py$"
    r"|.*\.(test|spec)\.[cm]?[jt]sx?$"
    r"|.*_test\.go$"
    r"|.*_spec\.rb$|.*_test\.rb$"
    r"|.*Tests?\.(java|kt|cs|php)$"
    r"|.*_test\.(lua|gd|rs)$)"
)
_CODE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".go", ".rb", ".java", ".kt",
    ".cs", ".php", ".rs", ".lua", ".gd",
}

# A change to one of these can affect any test without an import edge to it.
_RUN_ALL_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "pytest.ini", "noxfile.py",
    "package.json", "pnpm-workspace.yaml", "go.mod", "go.sum", "Cargo.toml", "Gemfile", "composer.json",
    "requirements.txt", "requirements-dev.txt", "Pipfile", "poetry.lock", "uv.lock",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb",
}
_RUN_ALL_PATTERN = re.compile(r"^(tsconfig.*\.json|jsconfig\.json|(jest|vitest|vite|babel|webpack)\.config\.[cm]?[jt]s|\.babelrc.*)$")


def is_test_path(path: str) -> bool:
    name = posixpath.basename(path)
    if posixpath.splitext(name)[1] not in _CODE_EXT or name == "conftest.py":
        return False
    if _TEST_NAME.match(name):
        return True
    folders = path.split("/")[:-1]
    if "__tests__" in folders:
        return True
    if posixpath.splitext(name)[1] in _NAME_ONLY_EXT:
        return False
    return any(part in _TEST_DIRS for part in folders)


def forces_run_all(path: str) -> bool:
    name = posixpath.basename(path)
    return name in _RUN_ALL_NAMES or bool(_RUN_ALL_PATTERN.match(name))


def implicit_dependencies(path: str, all_paths: set[str]) -> list[str]:
    """Files a test depends on without importing them."""
    deps = []
    if path.endswith(".py"):
        folder = posixpath.dirname(path)
        while True:
            conftest = posixpath.join(folder, "conftest.py") if folder else "conftest.py"
            if conftest in all_paths and conftest != path:
                deps.append(conftest)
            if not folder:
                break
            folder = posixpath.dirname(folder)
    elif path.endswith("_test.go"):
        folder = posixpath.dirname(path)
        deps.extend(
            p for p in sorted(all_paths)
            if posixpath.dirname(p) == folder and p.endswith(".go") and not p.endswith("_test.go")
        )
    return deps


def run_command(test_paths: list[str], package_json: dict | None = None) -> list[str]:
    """Shell commands that run exactly `test_paths`, one per ecosystem."""
    commands = []
    py = [p for p in test_paths if p.endswith(".py")]
    if py:
        commands.append("pytest " + " ".join(py))
    js = [p for p in test_paths if re.search(r"\.[cm]?[jt]sx?$", p)]
    if js:
        deps = {}
        for key in ("dependencies", "devDependencies"):
            deps.update((package_json or {}).get(key) or {})
        runner = "npx vitest run" if "vitest" in deps else "npx jest"
        commands.append(f"{runner} " + " ".join(js))
    go_dirs = sorted({posixpath.dirname(p) or "." for p in test_paths if p.endswith(".go")})
    if go_dirs:
        commands.append("go test " + " ".join(f"./{d}" if d != "." else "." for d in go_dirs))
    return commands


def package_inits(path: str, all_paths: set[str]) -> list[str]:
    """`__init__.py` files Python runs before `path` itself: importing
    a/b/c.py executes a/__init__.py and a/b/__init__.py first. Stops at the
    first folder that isn't a regular package (namespace / src roots)."""
    if not path.endswith(".py"):
        return []
    inits = []
    folder = posixpath.dirname(path)
    if posixpath.basename(path) == "__init__.py":
        folder = posixpath.dirname(folder)
    while folder:
        init = folder + "/__init__.py"
        if init not in all_paths:
            break
        inits.append(init)
        folder = posixpath.dirname(folder)
    return inits
