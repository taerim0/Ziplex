"""Single source of truth for where this package sits relative to a real
dev checkout's repo root. checkpoint.py's CHECKPOINT_DIR, packager.py's
RESULT_DIR, and doctor.py's _PYPROJECT_PATH each need "three levels up from
this file" (src/ziplex/<module>.py -> src/ziplex -> src -> repo root) and
used to compute it independently -- a future change to the package's own
directory depth would otherwise have to be hand-applied to every copy
instead of changing once here.

For a real installed package (pip install, no bundled pyproject.toml),
REPO_ROOT still resolves to *some* directory (three levels above wherever
site-packages/ziplex/ actually is), just not a real repo -- doctor.py's own
_min_python() already documents and handles that degrade; this module makes
no promise the resulting path exists or is meaningful outside a dev
checkout.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
