"""Small statistics helpers shared by the eval pipeline and the notebook API."""

from __future__ import annotations

import math
from typing import Tuple


def wilson_interval(passed: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """95% Wilson score interval for a pass rate of ``passed`` out of ``total``.

    Returned as ``(low, high)`` in [0, 1]. ``(0.0, 0.0)`` when ``total`` is 0.
    A rate reported without its interval is how thirty cases look like a
    result; this is cheap enough to attach to every rate the framework prints.
    """
    if total <= 0:
        return (0.0, 0.0)
    p = passed / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))
