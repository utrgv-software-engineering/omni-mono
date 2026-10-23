"""Whether a judge can be trusted, and what its verdicts say once it is.

A judge is validated against people's labels: its true positive rate
(TPR, how often it says Pass when a person said Pass) and true negative
rate (TNR, how often it says Fail when a person said Fail), measured once
on its judge dataset's held-out test split. Pass is the positive class.

That measurement is recorded beside the judge, in
``judges/<key>/validation.yml``, for one version of the judge (a hash of its
settings, instructions and template) and one test split. Changing the judge
makes it a new, unvalidated version; the test split of a version is
measured once (:func:`check_test_unspent`), so its score cannot become one
more number to tune against.

A run's raw pass rate from a judge is biased by the judge's errors. For a
validated judge, :func:`judge_report` gives the corrected rate
(Rogan-Gladen: ``(p_obs + TNR - 1) / (TPR + TNR - 1)``) with a bootstrap
95% interval that resamples both the run's verdicts and the test labels.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from omniagents.core.eval.layout import EvalLayout

VALIDATION_FILE = "validation.yml"
#: Below this, TPR + TNR - 1 leaves the judge too close to chance for its
#: verdicts to be corrected.
MIN_INFORMEDNESS = 0.05
BOOTSTRAP_SAMPLES = 2000


class TestSplitSpent(ValueError):
    """This version of the judge was already measured on this test split."""


def confusion(pairs: Iterable[Tuple[bool, Optional[bool]]]) -> Dict[str, Any]:
    """The judge's agreement with people, Pass being positive. ``pairs`` are
    ``(person said Pass, judge said Pass)``; a judge verdict of ``None`` (no
    parsable answer) counts as unanswered, not as either."""
    tp = fn = tn = fp = unanswered = 0
    for human_pass, judge_pass in pairs:
        if judge_pass is None:
            unanswered += 1
        elif human_pass and judge_pass:
            tp += 1
        elif human_pass:
            fn += 1
        elif judge_pass:
            fp += 1
        else:
            tn += 1
    return {
        "tp": tp,
        "fn": fn,
        "tn": tn,
        "fp": fp,
        "unanswered": unanswered,
        "tpr": tp / (tp + fn) if tp + fn else None,
        "tnr": tn / (tn + fp) if tn + fp else None,
    }


def corrected_rate(p_obs: float, tpr: float, tnr: float) -> Optional[float]:
    """The Rogan-Gladen estimate of the true pass rate from a judge's
    observed one, clipped to [0, 1]; ``None`` when the judge is too close
    to chance to correct."""
    informedness = tpr + tnr - 1
    if informedness < MIN_INFORMEDNESS:
        return None
    return min(1.0, max(0.0, (p_obs + tnr - 1) / informedness))


def corrected_interval(
    counts: Dict[str, int], passed: int, judged: int, *, seed: int = 0
) -> Optional[Tuple[float, float]]:
    """A bootstrap 95% interval for the corrected rate, resampling the test
    labels (and so TPR and TNR) and the run's verdicts. Seeded, so the same
    run reports the same interval."""
    if judged <= 0:
        return None
    rng = random.Random(seed)
    humans = [True] * (counts["tp"] + counts["fn"]) + [False] * (
        counts["tn"] + counts["fp"]
    )
    judges = (
        [True] * counts["tp"]
        + [False] * counts["fn"]
        + [False] * counts["tn"]
        + [True] * counts["fp"]
    )
    n = len(humans)
    if not n:
        return None
    p_obs = passed / judged
    estimates: List[float] = []
    for _ in range(BOOTSTRAP_SAMPLES):
        picks = [rng.randrange(n) for _ in range(n)]
        sample = confusion((humans[i], judges[i]) for i in picks)
        if sample["tpr"] is None or sample["tnr"] is None:
            continue
        p = sum(1 for _ in range(judged) if rng.random() < p_obs) / judged
        value = corrected_rate(p, sample["tpr"], sample["tnr"])
        if value is not None:
            estimates.append(value)
    if len(estimates) < BOOTSTRAP_SAMPLES // 2:
        return None
    estimates.sort()
    low = estimates[int(0.025 * len(estimates))]
    high = estimates[min(len(estimates) - 1, int(0.975 * len(estimates)))]
    return (low, high)


def judge_version(cfg: Dict[str, Any]) -> str:
    """A judge's version: a hash of its settings and the instructions and
    template it reads. Any change to them is a new, unvalidated version."""
    parts = [json.dumps(cfg, sort_keys=True, default=str)]
    for block, key in (("agent", "instructions_file"), ("input", "template_file")):
        value = (cfg.get(block) or {}).get(key)
        if isinstance(value, str) and Path(value).is_file():
            parts.append(Path(value).read_text(encoding="utf-8"))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def validation_path(layout: EvalLayout, key: str) -> Path:
    return layout.judge_dir(key) / VALIDATION_FILE


def read_validation(layout: EvalLayout, key: str) -> Optional[Dict[str, Any]]:
    path = validation_path(layout, key)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def check_test_unspent(
    layout: EvalLayout, key: str, version: str, test_split: Path
) -> None:
    """Refuse a second test measurement of the same judge version on the
    same test split."""
    recorded = read_validation(layout, key)
    if (
        recorded
        and recorded.get("judge_version") == version
        and recorded.get("test_split") == _file_hash(test_split)
    ):
        raise TestSplitSpent(
            f"Judge {key!r} as it is now was already measured on this test split "
            f"({recorded.get('validated_at')}): TPR {recorded['test']['tpr']}, "
            f"TNR {recorded['test']['tnr']}. Iterate on the dev split; the test "
            "split is measured once per version of the judge."
        )


def record_validation(
    layout: EvalLayout,
    key: str,
    cfg: Dict[str, Any],
    test_split: Path,
    counts: Dict[str, Any],
    *,
    source: str,
) -> Dict[str, Any]:
    """Write ``judges/<key>/validation.yml``: the judge version measured, on
    which test split, and its confusion counts, TPR and TNR."""
    record = {
        "judge_version": judge_version(cfg),
        "model": (cfg.get("agent") or {}).get("model"),
        "category": cfg.get("failure_category") or cfg.get("category"),
        "test_split": _file_hash(test_split),
        "test": {
            "tp": counts["tp"],
            "fn": counts["fn"],
            "tn": counts["tn"],
            "fp": counts["fp"],
            "unanswered": counts.get("unanswered", 0),
            "tpr": counts["tpr"],
            "tnr": counts["tnr"],
        },
        "source": source,
        "validated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = validation_path(layout, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return record


def is_test_split(layout: EvalLayout, split_path: Path) -> bool:
    """Whether ``split_path`` is a judge dataset's test split."""
    path = Path(split_path).resolve()
    return path.name == "test.yml" and path.parent.parent == (
        layout.judge_datasets_dir.resolve()
    )


def validation_status(
    layout: EvalLayout, key: str, cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """Whether the judge as it is now is validated: ``validated``,
    ``changed`` (validated, then edited) or ``unvalidated``, with the
    recorded measurement."""
    recorded = read_validation(layout, key)
    if recorded is None:
        return {"status": "unvalidated", "validation": None}
    current = recorded.get("judge_version") == judge_version(cfg)
    return {"status": "validated" if current else "changed", "validation": recorded}


def _verdict(output: Any) -> Optional[bool]:
    if isinstance(output, dict) and isinstance(output.get("answer"), str):
        answer = output["answer"].strip().lower()
        if answer in ("pass", "fail"):
            return answer == "pass"
    return None


def judge_report(
    layout: EvalLayout, judges: Dict[str, Any], runs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """For each judge that judged ``runs``: its raw pass rate and, when it is
    validated as it is now, the corrected rate with its interval."""
    report = []
    for key, cfg in sorted(judges.items()):
        verdicts = [
            _verdict((run.get("metadata") or {}).get(key))
            for run in runs
            if key in (run.get("metadata") or {})
        ]
        if not verdicts:
            continue
        answered = [v for v in verdicts if v is not None]
        passed = sum(1 for v in answered if v)
        raw = passed / len(answered) if answered else None
        status = validation_status(layout, key, cfg if isinstance(cfg, dict) else {})
        entry: Dict[str, Any] = {
            "judge": key,
            "status": status["status"],
            "judged": len(verdicts),
            "answered": len(answered),
            "passed": passed,
            "raw_pass_rate": raw,
            "corrected_pass_rate": None,
            "corrected_interval": None,
        }
        recorded = status["validation"]
        if status["status"] == "validated" and raw is not None:
            test = recorded["test"]
            entry["tpr"], entry["tnr"] = test["tpr"], test["tnr"]
            if test["tpr"] is not None and test["tnr"] is not None:
                entry["corrected_pass_rate"] = corrected_rate(
                    raw, test["tpr"], test["tnr"]
                )
                interval = corrected_interval(test, passed, len(answered))
                entry["corrected_interval"] = list(interval) if interval else None
        report.append(entry)
    return report
