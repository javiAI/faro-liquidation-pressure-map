"""Make the `liqmap` package importable when tests run without an editable install.

(pyproject's [tool.pytest.ini_options] pythonpath=["src"] also covers this; this is a
belt-and-suspenders fallback for older pytest.)
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
