"""Stratified holdout for agent evaluation.

Distinct from :mod:`omniagents.core.eval.split`, which partitions scenarios by
a failure-category *label* to train and test a judge. This partitions a
scenario set for evaluating an *agent*: a seeded, stratified train/dev/test
assignment by scenario name, written as an assignment (names only, never a
copy of the scenarios) in the evaluations directory's ``holdout/``, and selected at
run time with ``--split test``. The manifest records the seed, the strata and
the counts, so "the test split was touched once" is checkable.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import yaml

from omniagents.core.eval.layout import SPLIT_NAMES, EvalLayout


def scenario_key(scenario: Dict[str, Any]) -> str:
    """The name a scenario is assigned under (``name``, else ``id``)."""
    return str(scenario.get("name") or scenario.get("id") or "")


def field_value(scenario: Dict[str, Any], path: str) -> str:
    """A scenario field by dotted path, as a string suitable for grouping.

    ``expect.skin_tone`` reads a nested key; ``tags`` (a list) becomes the
    sorted tags joined by ``|`` so a stratum is the whole tag set.
    """
    cur: Any = scenario
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ""
    if isinstance(cur, (list, tuple)):
        return "|".join(sorted(str(x) for x in cur))
    return "" if cur is None else str(cur)


def _allocate(n: int, fractions: Sequence[float]) -> List[int]:
    """Largest-remainder allocation of ``n`` items across the fractions; sums to ``n``."""
    raw = [f * n for f in fractions]
    counts = [int(x) for x in raw]
    short = n - sum(counts)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in order[:short]:
        counts[i] += 1
    return counts


def create_holdout(
    scenarios: List[Dict[str, Any]],
    strata: Sequence[str] = (),
    fractions: Sequence[float] = (0.5, 0.25, 0.25),
    seed: int = 42,
) -> Dict[str, str]:
    """Assign every scenario to train, dev or test.

    Within each stratum (the tuple of ``strata`` field values) scenarios are
    shuffled with ``seed`` and split by ``fractions`` with largest-remainder
    rounding, so each stratum is represented in each split in proportion.
    The assignment is a function of (names, strata values, seed): re-running
    on the same scenarios gives the same split.
    """
    if (
        len(fractions) != 3
        or abs(sum(fractions) - 1.0) > 1e-6
        or any(f < 0 for f in fractions)
    ):
        raise ValueError(
            f"fractions must be three non-negative numbers summing to 1, got {list(fractions)}"
        )
    keyed = [(scenario_key(sc), sc) for sc in scenarios]
    missing = [sc for k, sc in keyed if not k]
    if missing:
        raise ValueError(
            f"{len(missing)} scenario(s) have neither name nor id and cannot be assigned"
        )
    names = [k for k, _ in keyed]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(
            f"scenario names must be unique to hold out by name; duplicates: {dupes[:5]}"
        )

    groups: Dict[Tuple[str, ...], List[str]] = {}
    for key, sc in keyed:
        stratum = tuple(field_value(sc, p) for p in strata)
        groups.setdefault(stratum, []).append(key)

    rng = random.Random(int(seed))
    assignment: Dict[str, str] = {}
    for stratum in sorted(groups):
        members = sorted(groups[stratum])
        rng.shuffle(members)
        counts = _allocate(len(members), fractions)
        start = 0
        for split_name, count in zip(SPLIT_NAMES, counts):
            for key in members[start : start + count]:
                assignment[key] = split_name
            start += count
    return assignment


def holdout_manifest(
    assignment: Dict[str, str],
    scenarios: List[Dict[str, Any]],
    strata: Sequence[str],
    fractions: Sequence[float],
    seed: int,
) -> Dict[str, Any]:
    """Counts per split and per stratum, plus everything needed to reproduce the assignment."""
    by_split: Dict[str, int] = {s: 0 for s in SPLIT_NAMES}
    by_stratum: Dict[str, Dict[str, int]] = {}
    for sc in scenarios:
        key = scenario_key(sc)
        split = assignment.get(key)
        if split is None:
            continue
        by_split[split] += 1
        stratum = "/".join(field_value(sc, p) for p in strata) or "(all)"
        by_stratum.setdefault(stratum, {s: 0 for s in SPLIT_NAMES})[split] += 1
    digest = hashlib.sha256("\n".join(sorted(assignment)).encode("utf-8")).hexdigest()[
        :16
    ]
    return {
        "seed": int(seed),
        "strata": list(strata),
        "fractions": [float(f) for f in fractions],
        "total": sum(by_split.values()),
        "counts": by_split,
        "counts_by_stratum": by_stratum,
        "scenario_names_sha": digest,
    }


def write_holdout(
    layout: EvalLayout, assignment: Dict[str, str], manifest: Dict[str, Any]
) -> Dict[str, Path]:
    """Write ``holdout/{train,dev,test}.yml`` and ``manifest.yml``."""
    out_dir = layout.holdout_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for split in SPLIT_NAMES:
        names = sorted(k for k, s in assignment.items() if s == split)
        path = out_dir / f"{split}.yml"
        path.write_text(
            yaml.safe_dump({"split": split, "scenarios": names}, sort_keys=False),
            encoding="utf-8",
        )
        paths[split] = path
    (out_dir / "manifest.yml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    paths["manifest"] = out_dir / "manifest.yml"
    return paths


def load_holdout(layout: EvalLayout, split: str) -> Tuple[List[str], Dict[str, Any]]:
    """Scenario names in ``split`` and the holdout manifest."""
    out_dir = layout.holdout_dir
    path = out_dir / f"{split}.yml"
    if split not in SPLIT_NAMES:
        raise ValueError(f"split must be one of {SPLIT_NAMES}, got {split!r}")
    if not path.is_file():
        raise FileNotFoundError(
            f"no holdout split {split!r} at {path}; create one with "
            "`omniagents eval scenarios holdout --strata <field,...> --seed <n>`"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    names = [str(n) for n in (data.get("scenarios") or [])]
    manifest_path = out_dir / "manifest.yml"
    manifest = (
        yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    return names, (manifest or {})
