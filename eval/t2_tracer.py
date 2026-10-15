"""pytest plugin used by t2_impacted_tests.py's ground truth (no Ziplex code).

Loaded with `-p t2_tracer` (its folder on PYTHONPATH) during
`pytest --collect-only <one test file>`. After collection it writes every
module in sys.modules whose file lives under T2_TRACE_ROOT -- the test
module, the conftest.py files pytest loaded for it, and everything they
imported, directly or through plugins -- as repo-relative paths to the JSON
file T2_TRACE_OUT, together with any collection errors.
"""
import json
import os
import sys

_errors: list[str] = []


def pytest_collectreport(report):
    if report.failed:
        _errors.append(f"{report.nodeid}: {str(report.longrepr)[-400:]}")


def pytest_collection_finish(session):
    root = os.path.realpath(os.environ["T2_TRACE_ROOT"])
    root_key = os.path.normcase(root) + os.sep
    files = set()
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        real = os.path.realpath(path)
        if os.path.normcase(real).startswith(root_key) and real.endswith(".py"):
            files.add(os.path.relpath(real, root).replace("\\", "/"))
    # Python test modules only (pytest.Function items); data-driven items a
    # conftest collects from non-test files (pygments' examplefiles) are not
    # test modules with an import set of their own.
    import pytest
    modules = set()
    for item in session.items:
        if isinstance(item, pytest.Function) and getattr(item, "module", None) is not None:
            real = os.path.realpath(item.module.__file__)
            if os.path.normcase(real).startswith(root_key):
                modules.add(os.path.relpath(real, root).replace("\\", "/"))
    with open(os.environ["T2_TRACE_OUT"], "w", encoding="utf-8") as f:
        json.dump({"files": sorted(files), "test_modules": sorted(modules), "errors": _errors}, f)
