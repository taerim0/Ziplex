"""Single source of truth for the tomllib/tomli import-availability
fallback. Python 3.11+ ships `tomllib` in the stdlib; 3.10 (this project's
own floor, per pyproject.toml's `requires-python`) needs the `tomli`
backport instead (identical `loads()`/`TOMLDecodeError` API, declared as a
conditional dependency in pyproject.toml for `python_version < "3.11"`, so
this is the normal path on 3.10, not a degrade).

tech_stack.py (reading a target project's own manifests) and doctor.py
(reading Ziplex's own pyproject.toml for `_min_python()`) each need this
exact fallback and used to carry two independently-maintained copies of the
same try/except -- consolidated here so a future concern (e.g. `tomli`
itself changing shape) only ever needs one fix.

`tomllib` is `None` only when neither the stdlib module nor the `tomli`
backport is importable at all (a broken/incomplete install) -- every real
caller already documents its own no-raise degrade for that case.
"""

try:
    import tomllib
except ImportError:  # Python < 3.11: no stdlib tomllib.
    try:
        import tomli as tomllib
    except ImportError:  # backport missing too -- callers degrade on
        # their own (see tech_stack.py's _load_toml()/doctor.py's
        # _min_python()).
        tomllib = None
