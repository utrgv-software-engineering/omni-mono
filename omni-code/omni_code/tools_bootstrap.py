from __future__ import annotations

import sys
from pathlib import Path


def ensure_tools_importable() -> None:
    base_dir = Path(__file__).resolve().parent.parent
    candidate = str(base_dir)
    if not candidate:
        return
    while candidate in sys.path:
        sys.path.remove(candidate)
    sys.path.insert(0, candidate)
