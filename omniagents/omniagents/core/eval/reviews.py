"""People's verdicts on evaluation answers, kept with the answers they judge.

A review is a person's judgement of one answer: one conversation of one
saved run (``group_id``, the trace group the run recorded it under). It is
written to ``reviews/<run id>.jsonl`` in the evaluations directory, one
line per reviewed conversation, and is committed:

- the verdict (``acceptable`` or ``unacceptable``), the failure categories
  seen, notes, who reviewed it and when;
- the scenario it ran and the answer as it was reviewed (``reply``, and the
  whole conversation as ``history`` when a judge of the project reads
  conversations), so a review stands on its own once the run is gone.

A judge dataset is built from reviews (:func:`build_judge_dataset`), so a
judge is always trained and checked on the very answer a person labelled.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from omniagents.core.eval.layout import DATASET_MANIFEST, SPLIT_NAMES, EvalLayout
from omniagents.core.eval.results import find_conversation
from omniagents.core.utils.yaml_edit import replace_file

VERDICTS = ("acceptable", "unacceptable")
UNSET: Any = object()
DATASET_SOURCE = "reviews"


class ReviewError(ValueError):
    """A review that cannot be recorded; the message says why."""


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write(path: Path, reviews: list[dict[str, Any]]) -> None:
    if not reviews:
        path.unlink(missing_ok=True)
        return
    ordered = sorted(
        reviews,
        key=lambda r: (_scenario_key(r), int(r.get("trial") or 0), r["group_id"]),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_file(
        path,
        "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in ordered
        ),
    )


def _scenario_key(review: dict[str, Any]) -> str:
    scenario = review.get("scenario") or {}
    return str(scenario.get("id") or scenario.get("name") or "")


def list_reviews(layout: EvalLayout) -> list[dict[str, Any]]:
    """Every review, run by run."""
    if not layout.reviews_dir.is_dir():
        return []
    reviews: list[dict[str, Any]] = []
    for path in sorted(layout.reviews_dir.glob("*.jsonl")):
        reviews.extend(_read(path))
    return reviews


def review_of(layout: EvalLayout, group_id: str) -> dict[str, Any] | None:
    for review in list_reviews(layout):
        if review.get("group_id") == group_id:
            return review
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save_review(
    layout: EvalLayout,
    group_id: str,
    *,
    reviewer: str | None,
    keep_history: bool,
    verdict: Any = UNSET,
    failure_categories: Any = UNSET,
    notes: Any = UNSET,
    marked: Any = UNSET,
) -> dict[str, Any] | None:
    """Record a person's review of the conversation ``group_id`` of a saved
    run (fields left ``UNSET`` keep what the review had). A review left with
    no verdict, categories, notes or mark is removed. Returns the review, or
    ``None`` when removed."""
    found = find_conversation(layout, group_id)
    existing = review_of(layout, group_id)
    if found is None and existing is None:
        raise ReviewError(
            f"No saved run has the conversation {group_id!r}; only evaluation "
            "answers whose run is saved are reviewed into the evaluations directory"
        )
    if verdict is not UNSET and verdict not in (None, *VERDICTS):
        raise ReviewError(f"A verdict is one of {', '.join(VERDICTS)}, got {verdict!r}")
    if existing is not None:
        review = dict(existing)
    else:
        entry, record = found["entry"], found["record"]
        scenario = {
            key: value
            for key, value in (record.get("scenario_config") or {}).items()
            if not str(key).startswith("_")
        }
        review = {
            "group_id": group_id,
            "run": entry["id"],
            "set": entry.get("set"),
            "workflow": entry.get("workflow_name"),
            "scenario": scenario,
            "trial": int(record.get("trial") or 0),
            "reply": record.get("final_assistant_text"),
        }
        if keep_history:
            review["history"] = record.get("history") or []
    if verdict is not UNSET:
        review["verdict"] = verdict
    if failure_categories is not UNSET:
        review["failure_categories"] = sorted(
            {str(c).strip() for c in failure_categories or [] if str(c).strip()}
        )
    if notes is not UNSET:
        review["notes"] = str(notes or "").strip() or None
    if marked is not UNSET:
        review["marked"] = bool(marked)
    review["reviewer"] = reviewer or review.get("reviewer")
    review["reviewed_at"] = _now()

    path = layout.review_file(review["run"])
    others = [r for r in _read(path) if r.get("group_id") != group_id]
    empty = not (
        review.get("verdict")
        or review.get("failure_categories")
        or review.get("notes")
        or review.get("marked")
    )
    _write(path, others if empty else [*others, review])
    return None if empty else review


def remove_review(layout: EvalLayout, group_id: str) -> None:
    review = review_of(layout, group_id)
    if review is None:
        raise ReviewError(f"No review of {group_id!r}")
    path = layout.review_file(review["run"])
    _write(path, [r for r in _read(path) if r.get("group_id") != group_id])


# -- judge datasets -------------------------------------------------------


def _order(group_id: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{group_id}".encode("utf-8")).hexdigest()


def _assign(
    examples: list[dict[str, Any]],
    prior: dict[str, str],
    seed: int,
    fractions: tuple[float, ...],
) -> dict[str, str]:
    """Each example's split, keeping every one the dataset already placed
    (so a judge's test examples never move into its training) and filling
    each label's splits toward ``fractions`` (so Pass and Fail answers are
    spread across train, dev and test in proportion). New examples are
    placed in an order fixed by the seed."""
    total = sum(fractions)
    share = {name: f / total for name, f in zip(SPLIT_NAMES, fractions)}
    assignment: dict[str, str] = {}
    for label in (True, False):
        members = [e for e in examples if e["label"] is label]
        counts = {name: 0 for name in SPLIT_NAMES}
        for example in members:
            if example["id"] in prior:
                assignment[example["id"]] = prior[example["id"]]
                counts[prior[example["id"]]] += 1
        fresh = sorted(
            (e for e in members if e["id"] not in prior),
            key=lambda e: _order(e["id"], seed),
        )
        for example in fresh:
            size = sum(counts.values()) + 1
            split = max(
                SPLIT_NAMES,
                key=lambda name: (
                    share[name] * size - counts[name],
                    -SPLIT_NAMES.index(name),
                ),
            )
            assignment[example["id"]] = split
            counts[split] += 1
    return assignment


def _prior_assignment(
    folder: Path, seed: int, fractions: tuple[float, ...]
) -> dict[str, str]:
    """Where the dataset in ``folder`` placed each example, when it was built
    with the same seed and fractions (otherwise it is built afresh)."""
    manifest_path = folder / DATASET_MANIFEST
    if not manifest_path.is_file():
        return {}
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if manifest.get("seed") != int(seed) or manifest.get("fractions") != [
        float(f) for f in fractions
    ]:
        return {}
    prior: dict[str, str] = {}
    for name in SPLIT_NAMES:
        path = folder / f"{name}.yml"
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for example in data.get("scenarios") or []:
                if isinstance(example, dict) and example.get("id"):
                    prior[str(example["id"])] = name
    return prior


#: Fewer than this many answers of a label in dev or test leaves TPR or TNR
#: too noisy to act on; validating a judge wants ~50 of each label in all.
MIN_PER_LABEL_IN_SPLIT = 10
TARGET_PER_LABEL = 50


def _dataset_warnings(counts: dict[str, dict[str, int]]) -> list[str]:
    warnings = []
    for key, label in (("passes", "Pass"), ("fails", "Fail")):
        total = sum(c[key] for c in counts.values())
        if total < TARGET_PER_LABEL:
            warnings.append(
                f"{total} {label} answers in all; aim for about {TARGET_PER_LABEL} "
                "of each label before trusting TPR and TNR"
            )
        for name in ("dev", "test"):
            if counts[name][key] < MIN_PER_LABEL_IN_SPLIT:
                warnings.append(f"{name} has {counts[name][key]} {label} answers")
    return warnings


def build_judge_dataset(
    layout: EvalLayout,
    category: str,
    *,
    fractions: tuple[float, float, float],
    seed: int,
) -> dict[str, Any]:
    """Write ``judge-datasets/<category>/`` from the reviews: every reviewed
    answer (one with a verdict or categories) is an example, labelled as
    failing (``label: true``) when reviewers saw ``category`` in it. Splits
    are balanced by label, and an example never moves once placed
    (:func:`_assign`). Returns the dataset's manifest."""
    if len(fractions) != 3 or sum(fractions) <= 0 or any(f < 0 for f in fractions):
        raise ReviewError(
            "Train, dev and test fractions must be three positive numbers"
        )
    examples: list[dict[str, Any]] = []
    for review in sorted(list_reviews(layout), key=lambda r: r["group_id"]):
        categories = review.get("failure_categories") or []
        if not (review.get("verdict") or categories):
            continue
        example = {
            **(review.get("scenario") or {}),
            "id": review["group_id"],
            "scenario_id": _scenario_key(review),
            "run": review.get("run"),
            "label": category in categories,
            "target_category": category,
            "reply": review.get("reply"),
            # The reviewer's notes: an example's critique when it is a judge's
            # few-shot example.
            "notes": review.get("notes"),
        }
        if review.get("history") is not None:
            example["history"] = review["history"]
        examples.append(example)
    if not examples:
        raise ReviewError("There are no reviewed answers to build a judge dataset from")

    folder = layout.judge_dataset_dir(category)
    assignment = _assign(
        examples, _prior_assignment(folder, seed, fractions), seed, fractions
    )
    splits: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLIT_NAMES}
    for example in examples:
        splits[assignment[example["id"]]].append(example)
    if folder.is_dir():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    class _Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:
            return True

    for name, members in splits.items():
        (folder / f"{name}.yml").write_text(
            yaml.dump(
                {"scenarios": members},
                Dumper=_Dumper,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    counts = {
        name: {
            "fails": sum(1 for e in members if e["label"]),
            "passes": sum(1 for e in members if not e["label"]),
        }
        for name, members in splits.items()
    }
    manifest = {
        "source": DATASET_SOURCE,
        "category": category,
        "seed": int(seed),
        "fractions": [float(f) for f in fractions],
        "counts": counts,
        "warnings": _dataset_warnings(counts),
        "built_at": _now(),
    }
    (folder / DATASET_MANIFEST).write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return manifest


def dataset_conversations(examples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """What a judge reads for each example of a dataset split, by its id:
    the answer as it was reviewed."""
    return {
        str(example["id"]): {
            "prompt": example.get("prompt"),
            "final_assistant_text": example.get("reply"),
            "history": example.get("history") or [],
        }
        for example in examples
        if example.get("id")
    }
