# eval/

Measurement scripts kept outside the package and the test suite. Ground truth
here must never reuse Ziplex's own resolver code. Plan and decision rules:
`docs/REVIEW-2026-10-14.md` §7; results go to `docs/EXPERIMENTS.md`.

## edges_python.py

Python import-edge accuracy of a pack against an `ast`-based ground truth.

```bash
ziplex pack <repo> --auto --auto-correct --no-llm --no-cache -o out/repo.json
python eval/edges_python.py <repo> out/repo.json --roots src,.
```

Known ground-truth blind spot: it resolves absolute imports only against the
given `--roots`. Ziplex also tries every ancestor folder of the importing file
(script directories, nested sub-projects under `examples/`), so edges it finds
that way show up as EXTRA. Read EXTRA lines before treating precision as a
resolver bug.

| Date | Repo | Commit | Recall | Precision | Notes |
|---|---|---|---|---|---|
| 2026-10-14 | Ziplex (self) | main `6f76137` | 252/252 (1.00) | 1.00 | was 203/244 before Phase 0 (scanner dropped 10 test files) |
| 2026-10-14 | modelcontextprotocol/python-sdk (held out) | `754254c` | 2698/2698 (1.00) | 0.96 | all 114 EXTRA under `examples/` via ancestor-folder roots; resolver-miss count 0 |

## t2_impacted_tests.py (T2, falsification row F2)

`Graph.impacted_tests(changed files)` on real commits vs. an import-trace
ground truth: per test module, a separate `pytest --collect-only -p t2_tracer
<test>` records every repo `.py` in `sys.modules` after collection (the
module, the conftest.py files pytest loaded, and all they import). A test is
truly impacted when that set holds a changed file. No Ziplex code on that
side; `t2_tracer.py` is the pytest plugin. Query = the commit's added/modified
non-test `.py` files (test files left out on both sides), tree at that commit,
one incrementally updated index across the commits.

```bash
git clone --filter=blob:none https://github.com/pallets/click <scratch>/repos/click
py -3.11 -m venv <scratch>/venvs/click
<scratch>/venvs/click/Scripts/python -m pip install -e <scratch>/repos/click pytest typing_extensions
python eval/t2_impacted_tests.py --repo <scratch>/repos/click \
    --python <scratch>/venvs/click/Scripts/python --work <scratch>/work/click --commits 25
```

Extra test deps used: flask `pytest asgiref python-dotenv`, rich `pytest
attrs`, pygments `pytest wcag-contrast-ratio`. The clone is checked out
detached at each commit (use a dedicated one); commits are picked from
`--ref` (default `origin/HEAD`). Ground truth is cached per commit under
`<work>/gt/`; results go to `<work>/t2_results.json`. A commit whose suite
doesn't collect at all is skipped and listed.

Columns: **recall** pooled over (commit, truly impacted test) pairs;
**ratio** = selected test modules / all test modules, median over commits;
**oracle** = the same ratio for the truly impacted set (the best any
import-level selector can do); **cf** = recall / median ratio of an ast-only
counterfactual selector that keeps only imports executed at import time (no
function bodies, `TYPE_CHECKING` or `__main__` blocks) and adds
module -> enclosing package `__init__.py` edges.

| Date | Repo (layout) | Commits (tip) | Test modules | Recall | Commits at recall 1.0 | Ratio (oracle) | Precision | cf recall / ratio | Index build / incr. update / query | Misses by class |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-15 | pallets/click (src) | 25 (`2247b35`) | 34-43 | 773/773 (1.000) | 25/25 | 1.00 (1.00) | 0.83 | 1.00 / 1.00 | 1.1s / 0.29s / 1.3ms | -- |
| 2026-10-15 | pallets/flask (src) | 24 + 1 skipped (`d086db85`) | 21-22 | 478/478 (1.000) | 24/24 | 1.00 (1.00) | 0.97 | 1.00 / 1.00 | 1.4s / 0.33s / 1.9ms | -- |
| 2026-10-15 | Textualize/rich (flat) | 25 (`9d8f9a3`) | 63 | 960/960 (1.000) | 25/25 | 0.84 (0.68) | 0.72 | 1.00 / 0.68 | 4.1s / 0.16s / 4.9ms | -- |
| 2026-10-15 | pygments/pygments (flat) | 25 (`156d85fd`) | 33 | 57/141 (0.404) | 2/25 | 0.00 (0.03) | 1.00 | 0.60 / 0.00 | 5.4s / 1.9s / 13ms | parent-package 27, runner 29, dynamic 28 |
| | **all 4** | **99** | | **2268/2352 (0.964)**; 0.988 without runner | 76/99 | **0.87 (0.81)** | | 0.976 / 0.81 | | |

Verdict for F2 (recall >= 0.98 and median ratio <= 0.6): **fails on both
halves as stated.**

- Recall is 1.000 on click/flask/rich and 0.40 on pygments. Pygments'
  84 missed pairs: **parent-package** (27, one commit editing
  `pygments/__init__.py`: `from pygments.lexers import X` runs
  `pygments/__init__.py` first, but the graph only has the edge to
  `lexers/__init__.py` -- fixable statically, the cf selector gets that
  commit 33/33); **runner** (29: pytest's own terminal writer imports
  `pygments.lexers.python`, so every pygments test loads it even with no
  test code -- inherent, no test->code graph can see it); **dynamic** (28:
  22 are `test_basic_api.py` parametrizing over every lexer/formatter via
  `_iter_lexerclasses()` / `getattr(formatters, name)` -- inherent to
  import-level selection; 6 are `from pygments.lexers import PythonLexer`
  served by the package's module `__getattr__` + `_mapping.py` -- fixable
  with a lazy-registry heuristic).
- Ratio is 1.00 on click/flask because the oracle is 1.00: every test
  imports the package, whose `__init__.py` imports nearly every module, so
  no import-level selector can go below it. The <= 0.6 threshold is not
  attainable on hub-`__init__` libraries; measure it against the oracle
  instead. Ziplex's excess over the oracle (rich 0.84 vs 0.68, click's
  `_termui_impl.py` commits 34 vs 2 tests, flask `testing.py` 22 vs 6) is
  function-local, `TYPE_CHECKING` and `__main__`-guard imports. The cf
  selector closes it at no import-level recall cost, but a function-local
  import *does* run once a test calls that function: keeping those edges
  (`cfk_*` in the results) puts rich's ratio at 1.00. So most of that
  "precision loss" is import-level ground truth undercounting runtime
  impact, not a Ziplex bug; only the `TYPE_CHECKING` / `__main__` edges are
  safe to drop.
- Ziplex also selects helpers that aren't test modules (`tests/render.py`,
  `tests/*/__init__.py`, flask's `examples/*/tests/` outside the configured
  testpaths); those are excluded from both sides of the metrics.
- Pygments' first-ever index build took 71s (cold disk on a fresh 2,800-file
  clone); 5.4s warm.
