"""Project-root-relative paths for the data / site / docs artifacts.

The package lives under ``src/liqmap/`` but the data it reads and the site it writes
live at the **repository root** (so GitHub Pages serves ``/docs`` and the history
accumulates in ``/data``). We resolve the root by walking up from this file until we
find a repo marker, so it is correct no matter where the package is imported from
(local checkout, editable install, or the GitHub Actions runner).
"""
from __future__ import annotations

import os

_MARKERS = ("requirements.txt", ".git", "pyproject.toml")


def _find_project_root(start: str) -> str:
    d = os.path.dirname(os.path.abspath(start))
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in _MARKERS):
            return d
        parent = os.path.dirname(d)
        if parent == d:                      # hit filesystem root: fall back to src/liqmap → repo
            return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(start))))
        d = parent


PROJECT_ROOT = _find_project_root(__file__)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SITE_DIR = os.path.join(PROJECT_ROOT, "site")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
