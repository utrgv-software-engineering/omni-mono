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


def _split_of(group_id: str, seed: int, fractions: tuple[float, ...]) -> str:
    """Which split an answer belongs in: a function of the answer and the
    seed only, so a review added later never moves an earlier one (a judge's
    test examples stay out of its training)."""
    digest = hashlib.sha256(f"{seed}:{group_id}".encode("utf-8")).hexdigest()
    point = int(digest[:12], 16) / float(16**12) * sum(fractions)
    edge = 0.0
    for name, fraction in zip(SPLIT_NAMES, fractions):
        edge += fraction
        if point < edge:
            return name
    return SPLIT_NAMES[-1]


def build_judge_dataset(
    layout: EvalLayout,
    category: str,
    *,
    fractions: tuple[float, float, float],
    seed: int,
) -> dict[str, Any]:
    """Write ``judge-datasets/<category>/`` from the reviews: every reviewed
    answer (one with a verdict or categories) is an example, positive when
    reviewers saw ``category`` in it. Returns the dataset's manifest."""
    if len(fractions) != 3 or sum(fractions) <= 0 or any(f < 0 for f in fractions):
        raise ReviewError(
            "Train, dev and test fractions must be three positive numbers"
        )
    splits: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLIT_NAMES}
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
        }
        if review.get("history") is not None:
            example["history"] = review["history"]
        splits[_split_of(review["group_id"], seed, fractions)].append(example)
    if not any(splits.values()):
        raise ReviewError("There are no reviewed answers to build a judge dataset from")

    folder = layout.judge_dataset_dir(category)
    if folder.is_dir():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    class _Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:
            return True

    for name, examples in splits.items():
        (folder / f"{name}.yml").write_text(
            yaml.dump(
                {"scenarios": examples},
                Dumper=_Dumper,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    manifest = {
        "source": DATASET_SOURCE,
        "category": category,
        "seed": int(seed),
        "fractions": [float(f) for f in fractions],
        "counts": {
            name: {
                "positives": sum(1 for e in examples if e["label"]),
                "negatives": sum(1 for e in examples if not e["label"]),
            }
            for name, examples in splits.items()
        },
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
