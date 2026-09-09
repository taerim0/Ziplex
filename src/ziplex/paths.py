"""Single source of truth for where this package sits relative to a real
dev checkout's repo root. checkpoint.py's CHECKPOINT_DIR, packager.py's
(now legacy-fallback-only) RESULT_DIR, and doctor.py's _PYPROJECT_PATH each
need "three levels up from this file" (src/ziplex/<module>.py -> src/ziplex
-> src -> repo root) and used to compute it independently -- a future
change to the package's own directory depth would otherwise have to be
hand-applied to every copy instead of changing once here.

For a real installed package (pip install, no bundled pyproject.toml),
REPO_ROOT still resolves to *some* directory (three levels above wherever
site-packages/ziplex/ actually is), just not a real repo -- doctor.py's own
_min_python() already documents and handles that degrade gracefully. Not
every REPO_ROOT-based path degrades so gracefully, though: packager.py's
RESULT_DIR used to be pack()'s actual default *save* location, and for a
real, non-editable install this resolves to somewhere three levels above
site-packages (verified directly: a venv's own Lib/ folder) -- a real,
confirmed bug for anyone doing a plain `pip install ziplex` and running
`ziplex pack` with no `-o`, not a hypothetical one. Fixed by making the
actual default project-relative instead (packager.DEFAULT_OUTPUT_SUBDIR);
RESULT_DIR itself is kept only as a last-resort fallback for a caller that
somehow has no project path to anchor to at all. This module still makes
no promise REPO_ROOT is meaningful outside a dev checkout -- anything built
on it should degrade the way doctor.py's own use does, not the way
RESULT_DIR's old role did.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
