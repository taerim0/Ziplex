"""Free (no LLM call), manifest-file-based tech-stack detection -- a
deterministic sibling to `llm.analyze_rules()`, not a replacement for it.
Scans a project's root directory (top-level only; monorepo submodules each
with their own manifest aren't chased) for known package-manager manifest
files and reads out language/ecosystem + a capped list of declared
top-level dependency names.

`rules` already gestures at a project's tech stack indirectly, by having an
LLM infer coding conventions from Tree-sitter signatures -- but that's a
guess from code *shape*, not a read of the actual manifest a package manager
would resolve. This is cheaper (no LLM call at all) and strictly more
accurate for the one narrow fact it covers: what's declared as a dependency,
not what conventions look like in practice.

Deliberately shallow: no lockfile resolution (package-lock.json/poetry.lock/
Cargo.lock/go.sum), no transitive dependency walking, no version-constraint
parsing beyond stripping it off the declared name. This is "what does this
project say it depends on," not a full dependency graph -- a lockfile-
accurate one would need per-ecosystem tooling this project has no reason to
vendor.

detect_tech_stack() never raises, by contract -- a manifest that exists but
can't be usefully read (malformed JSON/TOML/XML, or an unexpected shape a
real-world manifest can legally have, e.g. TOML's `project = "foo"` instead
of a `[project]` table) degrades to an empty dependency list for that
manifest's entry rather than aborting the caller. This is a convenience
fact block, not something pack() should abort over just because one
manifest is broken -- and, since ziplex's own floor moved from 3.11 to 3.10
(2026-08-24, verified against a real Python 3.10.11 install, not just
inferred -- pip's own metadata showed every runtime dependency, tree-sitter
itself included, already supports 3.10; nothing older is reachable since
tree-sitter is the one dependency with no lower floor at all), TOML parsing
now falls back to `tomli` (declared as a conditional dependency below for
`python_version < "3.11"`) rather than going straight to the empty-list
degrade the moment stdlib `tomllib` (3.11+ only) is missing. That degrade
path still exists as a last-resort safety net -- a dev environment that
skipped dependency resolution somehow, say -- and stays exactly as
documented: a pyproject.toml/Cargo.toml still gets an entry (its ecosystem
is known from the filename alone), just with an empty dependency list, the
same as a manifest that failed to parse for any other reason. Indistinguishable
in the output from a project that genuinely declares no dependencies --
not fixed with a separate "could not read" flag, since with `tomli` declared
as a real dependency this path is no longer the normal case for any
supported Python version, just a fallback for a broken install.
"""

import functools
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .file.textutil import read_text as _safe_read_text
from .toml_compat import tomllib

# Caps dependency list length per manifest so a project with hundreds of npm
# packages doesn't blow up aif.json's project section for a field that's
# meant to be a quick fact block, not a full lockfile dump.
MAX_DEPENDENCIES = 40


def _read_text(path: Path) -> str | None:
    # Delegates to file/textutil.py's shared safe-read rather than a local
    # path.read_text(encoding="utf-8") -- that only ever caught OSError,
    # not UnicodeDecodeError, breaking this module's own "never raises, by
    # contract" guarantee for a non-UTF-8 manifest (a real case for a
    # legacy Windows-authored file). _safe_read_text() already handles
    # both.
    return _safe_read_text(str(path))


def _load_json(path: Path) -> dict:
    text = _read_text(path)
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


@functools.lru_cache(maxsize=32)
def _load_toml(path: Path) -> dict:
    # Cached because pyproject.toml is read through here twice per
    # detect_tech_stack() call -- once via _parse_pyproject_toml() (the
    # dependency list) and once via _pyproject_package_manager() (poetry
    # vs. pip detection) -- a real, if low-cost, duplicate parse on every
    # single pack, caught by code review. detect_tech_stack() clears this
    # cache on entry, so the cache's lifetime never outlives one call and a
    # manifest edited between two packs of the same project (a real GUI
    # scenario -- the process stays alive across repacks) is never served
    # stale content from an earlier call.
    if tomllib is None:
        return {}
    text = _read_text(path)
    if text is None:
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _dig(data: dict, *keys: str) -> dict:
    """Walks a chain of dict.get() calls, returning {} the moment any
    intermediate value isn't a dict -- so a manifest with an unexpected
    shape at some level (TOML's `project = "foo"` instead of a `[project]`
    table; a `"dependencies": true` in a JSON manifest) degrades to "no
    dependencies found" there instead of raising AttributeError/TypeError.
    """
    for key in keys:
        if not isinstance(data, dict):
            return {}
        data = data.get(key)
    return data if isinstance(data, dict) else {}


def _string_items(value) -> list[str]:
    """dict/list -> its string entries, in order; anything else -> []. Used
    wherever a manifest field is expected to be a dict of names (JSON/TOML
    dependency tables, whose values legally include non-string versions,
    e.g. Cargo.toml's `serde = { version = "1.0" }`) or a list of strings
    (PEP 621's `dependencies = [...]`) but a malformed or unusually-shaped
    real-world manifest might not actually have that shape.
    """
    if isinstance(value, dict):
        return [k for k in value if isinstance(k, str)]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _parse_package_json(path: Path) -> list[str]:
    data = _load_json(path)
    return _string_items(data.get("dependencies")) + _string_items(data.get("devDependencies"))


def _parse_requirements_txt(path: Path) -> list[str]:
    text = _read_text(path)
    if text is None:
        return []
    names = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # A VCS/direct-URL requirement (pip's documented syntax, with or
        # without the "-e" editable-install flag) -- the real package name
        # lives in an optional "#egg=name" fragment on the *original* line,
        # not before whatever "#" happens to appear first, so this has to
        # be checked before the generic comment-stripping below would
        # destroy that fragment (or, without it, mangle the whole URL into
        # a garbage "dependency" name).
        if re.match(r"^(-e\s+)?(git|hg|svn|bzr)\+", stripped) or "://" in stripped:
            egg_match = re.search(r"[#&]egg=([A-Za-z0-9_.\-]+)", stripped)
            if egg_match:
                names.append(egg_match.group(1))
            continue

        line = stripped.split("#", 1)[0].strip()
        if not line or line.startswith(("-r", "-e", "--", "-c")):
            continue
        # strip version specifiers/extras/env markers: "flask[async]>=2.0; python_version>='3.8'" -> "flask"
        name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip()
        if name:
            names.append(name)
    return names


def _parse_pyproject_toml(path: Path) -> list[str]:
    data = _load_toml(path)

    names = []
    # PEP 621: [project.dependencies], a list of PEP 508 requirement strings.
    for dep in _string_items(_dig(data, "project").get("dependencies")):
        name = re.split(r"[<>=!~\[; ]", dep, maxsplit=1)[0].strip()
        if name:
            names.append(name)
    # Poetry: [tool.poetry.dependencies], a table keyed by name (including a
    # "python" entry for the interpreter constraint itself -- not a real
    # dependency, excluded).
    poetry_deps = _string_items(_dig(data, "tool", "poetry", "dependencies"))
    names.extend(name for name in poetry_deps if name.lower() != "python")
    return names


def _parse_cargo_toml(path: Path) -> list[str]:
    data = _load_toml(path)  # _load_toml() always returns a dict, never raises
    return _string_items(data.get("dependencies"))


def _parse_go_mod(path: Path) -> list[str]:
    text = _read_text(path)
    if text is None:
        return []
    names = []
    in_require_block = False
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if line.startswith("require ("):
            # A require(...) block's closing ")" is usually on its own line
            # further down -- but "require ()" (an empty block) or, less
            # commonly, a single-line "require (foo v1.0)" put both the "("
            # and ")" on this same line. Handling that here, instead of
            # only ever looking for a standalone ")" line, matters: without
            # it, an empty inline block left in_require_block permanently
            # True, silently misparsing every later line in the file
            # (including `replace`/`exclude` directives) as a dependency.
            remainder = line[len("require ("):].rstrip()
            if remainder.endswith(")"):
                inner = remainder[:-1].strip()
                if inner:
                    names.append(inner.split()[0])
                continue
            in_require_block = True
            continue
        if in_require_block:
            if line == ")":
                in_require_block = False
            elif line:
                names.append(line.split()[0])
        elif line.startswith("require "):
            parts = line[len("require "):].split()
            if parts:
                names.append(parts[0])
    return names


def _parse_gemfile(path: Path) -> list[str]:
    text = _read_text(path)
    if text is None:
        return []
    return re.findall(r'^\s*gem\s+["\']([^"\']+)["\']', text, re.MULTILINE)


def _parse_composer_json(path: Path) -> list[str]:
    data = _load_json(path)
    names = _string_items(data.get("require")) + _string_items(data.get("require-dev"))
    # "php" itself is a platform-version constraint, not a real dependency.
    return [n for n in names if n != "php"]


# Strips a default (unprefixed) XML namespace declaration -- shared by
# _parse_pom_xml() (Maven's own default namespace) and _parse_csproj()
# (legacy pre-.NET-Core MSBuild's) so plain tag/attribute lookups work
# without namespace-prefixed XPath, which ElementTree's limited XPath
# subset can't express for a default namespace anyway. Matches either
# quote style -- a hand-authored legacy manifest occasionally uses single
# quotes, even though double-quoted is what every real toolchain emits.
_DEFAULT_XMLNS_RE = re.compile(r"""\sxmlns=['"][^'"]*['"]""")


def _strip_default_xmlns(content: str) -> str:
    return _DEFAULT_XMLNS_RE.sub("", content, count=1)


def _parse_pom_xml(path: Path) -> list[str]:
    content = _read_text(path)
    if content is None:
        return []
    # Strip the default Maven namespace declaration so plain tag lookups
    # ("dependencies"/"dependency"/"artifactId") work without namespace-
    # prefixed XPath, which ElementTree's limited XPath subset can't express
    # for a default (unprefixed) namespace anyway.
    content = _strip_default_xmlns(content)
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    # Only the project's own direct top-level <dependencies> -- not `.//`,
    # which would also match entries nested under <dependencyManagement>
    # (BOM imports and version-management-only declarations, not things the
    # project actually depends on -- a common enough real Maven pattern that
    # matching it broadly would misreport it as a real dependency).
    deps_el = root.find("dependencies")
    if deps_el is None:
        return []
    names = []
    for dep in deps_el.findall("dependency"):
        artifact = dep.find("artifactId")
        if artifact is not None and artifact.text:
            names.append(artifact.text.strip())
    return names


def _parse_csproj(path: Path) -> list[str]:
    content = _read_text(path)
    if content is None:
        return []
    # Legacy (pre-.NET-Core) .csproj declares a default MSBuild namespace
    # (xmlns="http://schemas.microsoft.com/developer/msbuild/2003"); modern
    # SDK-style projects (the common case since .NET Core) have none. Strip
    # it the same way _parse_pom_xml() does, so a plain tag/attribute lookup
    # works for either shape without namespaced XPath.
    content = _strip_default_xmlns(content)
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    names = []
    # root.iter(), not a fixed ItemGroup path -- a real multi-targeting
    # .csproj can carry several conditional <ItemGroup>s (per target
    # framework), and PackageReference can legally sit in any of them.
    for pkg_ref in root.iter("PackageReference"):
        include = pkg_ref.get("Include")
        if include:
            names.append(include)
    return names


# Gradle dependency-configuration keywords this recognizes -- the common
# ones for declaring a real external dependency; excludes constraint-only/
# platform-BOM-only configurations (same "don't count things that aren't a
# real used dependency" restraint _parse_pom_xml() applies to
# dependencyManagement). Matches both Groovy DSL (build.gradle) and Kotlin
# DSL (build.gradle.kts) call syntax, which only differ in whether the
# parens are present -- this regex doesn't care either way.
_GRADLE_CONFIG_RE = re.compile(
    r"\b(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testCompileOnly|testRuntimeOnly|kapt|annotationProcessor)\b"
)
# A Maven-style "group:artifact" or "group:artifact:version" coordinate
# string literal -- version (if present) is dropped, matching this module's
# existing "declared name minus version" convention everywhere else.
_GRADLE_COORD_RE = re.compile(r"""['"]([\w.\-]+:[\w.\-]+)(?::[\w.\-+]+)?['"]""")


def _parse_gradle(path: Path) -> list[str]:
    """Deliberately basic regex parsing, not a real Groovy/Kotlin-script
    evaluator (Gradle build files are executable code, not a declarative
    format any of this module's other parsers could fully replicate without
    vendoring a JVM). Only catches a dependency given as a plain
    "group:artifact:version" string literal -- misses one built from a
    version catalog reference (`libs.guava`), a `project(":module")`
    reference (internal, not an external dependency anyway), or one split
    across `group`/`name`/`version` map-notation keys. Still covers the
    large majority of real-world build.gradle files, the same "good enough,
    free fact block" tradeoff this whole module already makes elsewhere.
    """
    text = _read_text(path)
    if text is None:
        return []
    names = []
    for line in text.splitlines():
        if not _GRADLE_CONFIG_RE.search(line):
            continue
        match = _GRADLE_COORD_RE.search(line)
        if match:
            names.append(match.group(1))
    return names


def _pyproject_package_manager(path: Path) -> str:
    """pyproject.toml alone is ambiguous -- both PEP 621 (pip, or any other
    PEP 517 build backend reading the same [project] table) and Poetry
    declare dependencies in it, under different tables. A real Poetry
    project has [tool.poetry.dependencies]; distinguishing on that (rather
    than the flat "poetry/pip" label every pyproject.toml manifest used to
    get regardless of which one it actually is) costs nothing extra here --
    _load_toml()/_dig() already parse this same file the same safe,
    never-raises way _parse_pyproject_toml() does. "poetry/pip" survives
    only as the last-resort label for a manifest with neither table (e.g. a
    build-backend-only pyproject.toml declaring no dependencies at all, or
    one that failed to parse) -- genuinely ambiguous, not just unchecked.
    """
    data = _load_toml(path)
    if _dig(data, "tool", "poetry", "dependencies"):
        return "poetry"
    if _dig(data, "project"):
        return "pip"
    return "poetry/pip"


# (manifest filename, language/ecosystem label, package-manager label (a
# fixed string, or a callable taking the manifest Path and returning one --
# see _pyproject_package_manager above for why pyproject.toml needs that),
# parser)
_MANIFESTS = [
    ("package.json",     "JavaScript/TypeScript", "npm",         _parse_package_json),
    ("requirements.txt", "Python",                 "pip",         _parse_requirements_txt),
    ("pyproject.toml",   "Python",                 _pyproject_package_manager,  _parse_pyproject_toml),
    ("Cargo.toml",       "Rust",                    "cargo",       _parse_cargo_toml),
    ("go.mod",           "Go",                      "go modules",  _parse_go_mod),
    ("Gemfile",          "Ruby",                     "bundler",     _parse_gemfile),
    ("composer.json",    "PHP",                       "composer",    _parse_composer_json),
    ("pom.xml",          "Java",                       "maven",       _parse_pom_xml),
    ("build.gradle",     "Java/Kotlin",                 "gradle",      _parse_gradle),
    ("build.gradle.kts", "Java/Kotlin",                 "gradle",      _parse_gradle),
]

# .csproj is the one manifest _MANIFESTS' fixed-filename model can't express
# directly -- a real .csproj is always named after its own project
# (<ProjectName>.csproj), not a fixed filename the way every other manifest
# here is (a real, previously-documented gap: AGENTS.md's .cs language notes
# flagged this exact mismatch). Handled as its own top-level-only glob scan,
# same non-recursive scope every other manifest lookup here already has.
_CSPROJ_LANGUAGE = "C#"
_CSPROJ_PACKAGE_MANAGER = "nuget"


def _build_stack_entry(filename: str, language: str, package_manager, manifest_path: Path, parser) -> dict:
    """Runs one manifest through its parser and shapes the result dict --
    shared by both the fixed-filename _MANIFESTS loop and the .csproj glob
    scan below, so the dedup/truncation/error-handling logic can't drift
    between the two.
    """
    try:
        raw_deps = parser(manifest_path)
    except Exception:
        raw_deps = []

    if callable(package_manager):
        try:
            resolved_package_manager = package_manager(manifest_path)
        except Exception:
            # Generic, not "poetry/pip" -- that label is _pyproject_package_
            # manager()'s own Python-specific last resort, already returned
            # from inside that function for every case it actually expects
            # (this except only ever fires for the callable itself raising
            # unexpectedly). A hardcoded Python label here would mislabel a
            # future non-Python manifest that adopts the callable form (a
            # real gap caught by code review, currently dormant since
            # pyproject.toml is the only caller today).
            resolved_package_manager = "unknown"
    else:
        resolved_package_manager = package_manager

    # de-dupe while preserving order (a manifest can list the same name
    # twice, e.g. across dependencies/devDependencies)
    seen = set()
    deps = []
    for dep in raw_deps:
        if dep not in seen:
            seen.add(dep)
            deps.append(dep)

    return {
        "manifest": filename,
        "language": language,
        "package_manager": resolved_package_manager,
        "dependencies": deps[:MAX_DEPENDENCIES],
        "dependencies_truncated": len(deps) > MAX_DEPENDENCIES,
    }


def detect_tech_stack(root_path: str) -> list[dict]:
    """Scans root_path's top level for known package-manager manifest files.
    Returns one entry per manifest actually found, in _MANIFESTS' fixed
    order (plus any .csproj files last -- see their own note above) -- so
    output is stable across runs/platforms, not directory-listing order
    (which isn't guaranteed for _MANIFESTS' own entries; the .csproj glob is
    sorted explicitly for the same reason).

    Never raises (see module docstring): every parser above is already
    defensive about a manifest's *shape*, and the try/except here is a
    second, deliberately broad safety net on top of that -- a manifest file
    is arbitrary user/project input this function has no control over, and
    an unanticipated shape degrading to "no dependencies found" for just
    that one manifest is always the right failure mode for a convenience
    fact block, never a crash that takes down the rest of pack() with it.
    """
    # Scopes _load_toml()'s memoization (see its own comment) to this one
    # call -- a fresh detect_tech_stack() run always sees the manifest's
    # current on-disk content, never a previous call's cached read.
    _load_toml.cache_clear()

    root = Path(root_path)
    stacks = []
    for filename, language, package_manager, parser in _MANIFESTS:
        manifest_path = root / filename
        if not manifest_path.is_file():
            continue
        stacks.append(_build_stack_entry(filename, language, package_manager, manifest_path, parser))

    # .csproj: variable filename, so it can't live in _MANIFESTS above --
    # scanned separately, top-level only (same non-recursive scope every
    # other manifest here already has), sorted for output stability.
    try:
        csproj_files = sorted(root.glob("*.csproj"))
    except OSError:
        csproj_files = []
    for csproj_path in csproj_files:
        stacks.append(_build_stack_entry(
            csproj_path.name, _CSPROJ_LANGUAGE, _CSPROJ_PACKAGE_MANAGER, csproj_path, _parse_csproj
        ))

    return stacks
