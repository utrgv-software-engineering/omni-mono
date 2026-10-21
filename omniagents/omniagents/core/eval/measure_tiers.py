"""Tier-aware parsing for a scenario's ``measures`` field.

A scenario's ``measures`` may be either:

- A flat list of measure names (legacy shape). Every measure defaults to
  the ``DEFAULT_TIER`` and gates CI per-run, matching pre-tier behavior.
- A dict mapping tier name to a list of measure names. Each measure
  carries its tier; the framework uses the tier to decide whether per-run
  failures contribute to the CI gate.

Default tier semantics (see ``GATING_TIERS``):

- ``outcome``  — per-run failures gate CI.
- ``quality``  — per-run failures gate CI.
- ``guard``    — per-run failures gate CI.
- ``process``  — recorded for diagnostic / cross-tab analysis only;
  per-run failures do not gate CI.

Custom tier names are accepted (so projects can introduce their own
classifications) but they default to gating semantics — opt out by
listing the tier name in a project's gating policy if/when one exists.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Tier names recognized by the framework. Custom tiers are allowed but
# treated as gating unless explicitly excluded.
KNOWN_TIERS: Tuple[str, ...] = ("outcome", "quality", "process", "guard")

# Tiers whose per-run failures contribute to the CI gate.
GATING_TIERS: frozenset = frozenset(("outcome", "quality", "guard"))

# Tier assigned to legacy flat-list scenarios and to measures whose tier
# can't be determined.  Gates CI (matches pre-tier behavior).
DEFAULT_TIER: str = "outcome"


def parse_measures(value: Any) -> Tuple[List[str], Dict[str, str]]:
    """Return ``(flat_names, tier_by_name)`` from a scenario's ``measures`` field.

    Accepts list shape (legacy) or dict shape (tier-keyed). Returns an
    empty pair when ``value`` is ``None`` or an unrecognized type.

    If a name appears in multiple tiers (a misconfiguration), the last
    occurrence wins. Order in ``flat_names`` follows insertion order
    across tiers as dict iteration sees them.
    """
    if value is None:
        return [], {}

    if isinstance(value, list):
        names = [str(m) for m in value if m is not None]
        return names, {n: DEFAULT_TIER for n in names}

    if isinstance(value, dict):
        flat: List[str] = []
        tier_by_name: Dict[str, str] = {}
        for tier_name, names in value.items():
            tier = str(tier_name)
            for n in names or []:
                if n is None:
                    continue
                m = str(n)
                if m not in tier_by_name:
                    flat.append(m)
                tier_by_name[m] = tier
        return flat, tier_by_name

    return [], {}


def measure_names(value: Any) -> List[str]:
    """Convenience: return the flat list of measure names from a
    scenario's ``measures`` field, regardless of shape."""
    return parse_measures(value)[0]


def gates_ci(tier: Optional[str]) -> bool:
    """Whether a measure with this tier should gate CI on per-run failure.

    Known non-gating tiers (currently just ``process``) do not gate.
    Everything else — known gating tiers, unknown tier names, missing tier
    — gates CI. Unknown tiers gate so that typos and new custom tiers
    fail loudly rather than silently being treated as diagnostic.
    """
    if tier is None or tier == "":
        return True
    # Explicit non-gating tier set: anything not in here gates.
    NON_GATING = frozenset(t for t in KNOWN_TIERS if t not in GATING_TIERS)
    return tier not in NON_GATING


def tag_results(
    results: List[Dict[str, Any]],
    tier_by_name: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Stamp each result dict with the appropriate ``tier`` field.

    Mutates and returns ``results`` for convenience. Results whose name
    isn't in ``tier_by_name`` get ``DEFAULT_TIER``.
    """
    for r in results:
        name = r.get("name")
        if isinstance(name, str) and name:
            r["tier"] = tier_by_name.get(name, DEFAULT_TIER)
        else:
            r.setdefault("tier", DEFAULT_TIER)
    return results
