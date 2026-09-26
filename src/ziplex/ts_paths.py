"""Resolves TS/JS path aliases -- tsconfig.json/jsconfig.json
`compilerOptions.paths` (`"@/*": ["src/*"]`) and `baseUrl` -- and
monorepo workspace package imports (`@acme/ui/button`, via each
package.json's `exports`) to the concrete collected files they name.

Without this, `import { x } from '@/utils/x'` reached resolve_dependency()
as a bare specifier: not "./"-relative, so it went to the dotted stem
heuristic, which either marked it external or, worse, matched an unrelated
file sharing the last segment. Aliases are the norm in real app-shaped TS
projects (Next.js/Vite scaffolds ship `@/*` by default), so those graphs
were mostly empty -- and it's the kind of resolution an agent can't redo
with a one-line grep, which is where EXPERIMENTS #10 found a graph pays off.

Free (no LLM call) and precise, same design as go_packages.py: read the
project's own config, rewrite an alias only when it lands on an actually
collected file, leave everything else untouched. Deliberately partial:
the nearest ancestor tsconfig.json (else jsconfig.json) applies to a file
-- tsc's own `include`/`files`/project-references matching isn't modeled --
and a package-name `extends` (`"@tsconfig/node20"`, which lives in
node_modules) is skipped; only relative `extends` chains are followed.
Workspaces come from the root pnpm-workspace.yaml or package.json
`workspaces` only (no nested workspace roots, no Yarn PnP).
"""

import json
import posixpath
from pathlib import Path

from .file.relationship import resolve_module_path
from .file.textutil import read_text

_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
TS_SOURCE_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
_MAX_EXTENDS_DEPTH = 10


def _strip_jsonc(text: str) -> str:
    """tsconfig is JSONC: // and /* */ comments plus trailing commas, all
    legal there and all fatal to json.loads(). String-aware, so a "//"
    inside a path value (`"@/*"`, a URL) is kept."""
    out = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
        elif c == '"':
            in_string = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # trailing comma
                continue
            out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _load_json(root_path: str, rel_path: str) -> dict | None:
    text = read_text(str(Path(root_path) / rel_path))
    if text is None:
        return None
    try:
        data = json.loads(_strip_jsonc(text))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _resolve_extends(config_dir: str, ref: str, root_path: str) -> str | None:
    if not ref.startswith(("./", "../")):
        return None  # a package in node_modules -- not chased
    rel = posixpath.normpath(posixpath.join(config_dir, ref))
    for candidate in (rel, rel + ".json"):
        if (Path(root_path) / candidate).is_file():
            return candidate
    return None


def load_compiler_paths(root_path: str, config_rel: str, _depth: int = 0) -> dict:
    """The effective {"base_url", "paths", "paths_dir"} for one config file,
    all paths project-relative POSIX, after merging its `extends` chain.
    Mirrors tsc: a config's own baseUrl/paths override inherited ones, each
    resolved relative to the config that declared it; `paths` targets are
    relative to the effective baseUrl, or -- with none -- to the config
    that declared `paths`. Empty dict for an unreadable config."""
    data = _load_json(root_path, config_rel)
    if data is None or _depth > _MAX_EXTENDS_DEPTH:
        return {}
    config_dir = posixpath.dirname(config_rel)

    merged: dict = {}
    extends = data.get("extends")
    for ref in extends if isinstance(extends, list) else [extends]:
        if isinstance(ref, str):
            parent = _resolve_extends(config_dir, ref, root_path)
            if parent:
                merged.update(load_compiler_paths(root_path, parent, _depth + 1))

    options = data.get("compilerOptions")
    options = options if isinstance(options, dict) else {}
    if isinstance(options.get("baseUrl"), str):
        merged["base_url"] = posixpath.normpath(posixpath.join(config_dir, options["baseUrl"]))
    if isinstance(options.get("paths"), dict):
        merged["paths"] = options["paths"]
        merged["paths_origin"] = config_dir
    if "paths" in merged:
        merged["paths_dir"] = merged.get("base_url", merged["paths_origin"])
    return merged


def _match_pattern(pattern: str, dep: str) -> str | None:
    """The `*` capture if `dep` matches a paths key, "" for an exact key,
    None for no match. A key holds at most one `*` (tsc's own rule)."""
    if "*" not in pattern:
        return "" if pattern == dep else None
    prefix, _, suffix = pattern.partition("*")
    if dep.startswith(prefix) and dep.endswith(suffix) and len(dep) >= len(prefix) + len(suffix):
        return dep[len(prefix):len(dep) - len(suffix)]
    return None


def resolve_alias(dep: str, options: dict, all_names: set) -> str | None:
    """One specifier through `paths` (longest matching prefix wins, exact
    keys first, each substitution tried in order), then `baseUrl`. The
    collected file name on a hit, None otherwise."""
    paths = options.get("paths")
    if isinstance(paths, dict):
        matches = []
        for pattern, targets in paths.items():
            capture = _match_pattern(pattern, dep)
            if capture is not None and isinstance(targets, list):
                exact = "*" not in pattern
                matches.append((exact, len(pattern.partition("*")[0]), capture, targets))
        for _, _, capture, targets in sorted(matches, key=lambda m: (m[0], m[1]), reverse=True):
            for target in targets:
                if isinstance(target, str):
                    joined = posixpath.join(options["paths_dir"], target.replace("*", capture, 1))
                    resolved = resolve_module_path(joined, all_names)
                    if resolved:
                        return resolved
    base_url = options.get("base_url")
    if base_url is not None:
        return resolve_module_path(posixpath.join(base_url, dep), all_names)
    return None


def _workspace_globs(root_path: str) -> list[str]:
    """Workspace package globs from pnpm-workspace.yaml, else the root
    package.json's `workspaces` (npm/yarn/bun: a list, or {"packages": [...]}).
    Root-level only, like go.mod in go_packages.py."""
    text = read_text(str(Path(root_path) / "pnpm-workspace.yaml"))
    if text is not None:
        from ruamel.yaml import YAML
        from ruamel.yaml.error import YAMLError
        try:
            data = YAML(typ="safe").load(text)
        except YAMLError:
            data = None
        packages = data.get("packages") if isinstance(data, dict) else None
        if isinstance(packages, list):
            return [p for p in packages if isinstance(p, str)]
    data = _load_json(root_path, "package.json") or {}
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    return [p for p in workspaces if isinstance(p, str)] if isinstance(workspaces, list) else []


def build_workspace_packages(root_path: str) -> dict[str, tuple[str, dict]]:
    """{package name: (project-relative dir, its package.json)} for every
    workspace package. `!`-negated globs and node_modules are skipped."""
    root = Path(root_path)
    packages: dict[str, tuple[str, dict]] = {}
    for pattern in _workspace_globs(root_path):
        if pattern.startswith("!"):
            continue
        pattern = posixpath.normpath(pattern.replace("\\", "/"))
        if pattern == ".":
            folders = [root]  # hono's pnpm-workspace.yaml lists the root itself
        elif pattern.startswith(("../", "/")) or pattern == "..":
            continue  # outside the packed project
        else:
            try:
                folders = sorted(root.glob(pattern))
            except (ValueError, IndexError, OSError):
                continue  # a glob pathlib can't parse -- never fail a pack over it
        for folder in folders:
            if "node_modules" in folder.parts or not folder.is_dir():
                continue
            rel = folder.relative_to(root).as_posix()
            data = _load_json(root_path, f"{rel}/package.json")
            if data and isinstance(data.get("name"), str):
                packages.setdefault(data["name"], (rel, data))
    return packages


def _export_targets(value) -> list[str]:
    """Every file path an `exports` entry can point at, in declaration
    order -- a condition object (`{"types": ..., "default": ...}`) or a
    fallback array flattens to its leaves. The caller keeps the first that
    is a collected file, so a `types` entry pointing at an uncommitted
    dist/ .d.ts is skipped for the `default` source file behind it."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _export_targets(v)]
    if isinstance(value, list):
        return [t for v in value for t in _export_targets(v)]
    return []


def resolve_workspace_import(dep: str, packages: dict, all_names: set) -> str | None:
    """`@acme/ui` / `@acme/ui/button` -> the workspace file it names, via
    the package's `exports` (subpath keys, one `*` wildcard allowed), else
    `main`/`module`/`index` for the root and a plain subpath otherwise."""
    for name in sorted(packages, key=len, reverse=True):
        if dep != name and not dep.startswith(name + "/"):
            continue
        folder, manifest = packages[name]
        subpath = "." + dep[len(name):]
        exports = manifest.get("exports")
        if exports is None:
            if subpath == ".":
                entries = [manifest.get(k) for k in ("module", "main", "types") if isinstance(manifest.get(k), str)]
                return next(
                    (hit for e in entries + ["index"] if (hit := resolve_module_path(posixpath.join(folder, e), all_names))),
                    None,
                )
            return resolve_module_path(posixpath.join(folder, subpath), all_names)
        if not isinstance(exports, dict) or not any(k.startswith(".") for k in exports):
            exports = {".": exports}  # bare string / array / condition object = root entry only
        targets = []
        if subpath in exports:
            targets = _export_targets(exports[subpath])
        else:
            for key, value in exports.items():
                capture = _match_pattern(key, subpath) if "*" in key else None
                if capture is not None:
                    targets = [t.replace("*", capture) for t in _export_targets(value)]
                    break
        for target in targets:
            hit = resolve_module_path(posixpath.join(folder, target), all_names)
            if hit:
                return hit
        return None
    return None


class TsPathIndex:
    """Per-pack lookup: which config governs a file, each config's
    effective options, and the workspace package map -- all computed
    lazily, once."""

    def __init__(self, root_path: str, all_names: list[str]):
        self.root_path = root_path
        self.all_names = set(all_names)
        self._config_for_dir: dict[str, str | None] = {}
        self._options: dict[str, dict] = {}
        self._packages: dict | None = None

    @property
    def packages(self) -> dict:
        if self._packages is None:
            self._packages = build_workspace_packages(self.root_path)
        return self._packages

    def _config_in(self, folder: str) -> str | None:
        if folder not in self._config_for_dir:
            found = None
            for config_name in _CONFIG_NAMES:
                rel = posixpath.join(folder, config_name) if folder else config_name
                if (Path(self.root_path) / rel).is_file():
                    found = rel
                    break
            if found is None and folder:
                found = self._config_in(posixpath.dirname(folder))
            self._config_for_dir[folder] = found
        return self._config_for_dir[folder]

    def options_for(self, name: str) -> dict:
        config = self._config_in(posixpath.dirname(name))
        if config is None:
            return {}
        if config not in self._options:
            self._options[config] = load_compiler_paths(self.root_path, config)
        return self._options[config]

    def rewrite(self, name: str, deps: list[str]) -> list[str]:
        """deps with every alias that lands on a collected file replaced by
        that file's name -- which resolve_dependency()'s exact-filename
        check then matches as-is. Relative specifiers and anything that
        doesn't resolve pass through untouched."""
        if not name.endswith(TS_SOURCE_EXTENSIONS):
            return deps
        options = self.options_for(name)
        has_alias = bool(options.get("paths")) or options.get("base_url") is not None
        if not has_alias and not self.packages:
            return deps
        rewritten = []
        for dep in deps:
            if dep.startswith((".", "/")) or dep in self.all_names:
                rewritten.append(dep)
                continue
            # tsc consults paths/baseUrl before node_modules, where a
            # workspace package lives (symlinked) -- same order here.
            resolved = resolve_alias(dep, options, self.all_names) if has_alias else None
            if resolved is None and self.packages:
                resolved = resolve_workspace_import(dep, self.packages, self.all_names)
            rewritten.append(resolved or dep)
        return rewritten


def resolve_ts_context(root_path: str, all_names: list[str]) -> TsPathIndex | None:
    """None for a project with no TS/JS files at all, so a non-TS pack never
    touches the filesystem for a config lookup."""
    if not any(n.endswith(TS_SOURCE_EXTENSIONS) for n in all_names):
        return None
    return TsPathIndex(root_path, all_names)
