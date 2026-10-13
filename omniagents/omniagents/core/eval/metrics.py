from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from omniagents.core.eval.stats import wilson_interval


def _deep_get(d: Any, path: Optional[str]) -> Any:
    cur = d
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _apply_target(
    value: Optional[float], target: Dict[str, Any] | None
) -> Optional[bool]:
    if value is None or not target:
        return None
    op = str(target.get("op", ">=")).strip()
    raw_target = target.get("value")
    if raw_target is None:
        return None
    try:
        tgt = float(raw_target)
    except Exception:
        return None
    if op == ">=":
        return value >= tgt
    if op == ">":
        return value > tgt
    if op == "<=":
        return value <= tgt
    if op == "<":
        return value < tgt
    if op == "==":
        return abs(value - tgt) < 1e-12
    if op == "!=":
        return abs(value - tgt) >= 1e-12
    return None


def _slice_keys(run: Dict[str, Any], field: str) -> List[str]:
    """Group keys for a run under ``field`` (a dotted path into its scenario).

    A list-valued field (``tags``) contributes one key per element, so a run
    tagged ``[dev, tone_34]`` lands in both groups.
    """
    value = _deep_get(run.get("scenario_config") or {}, field)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _answered(run: Dict[str, Any]) -> bool:
    """Whether the run produced a final answer. Runs recorded before the field existed count as answered."""
    if "answered" in run:
        return bool(run.get("answered"))
    text = run.get("final_assistant_text")
    return True if text is None else bool(str(text).strip())


def _rate_block(pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]]) -> Dict[str, Any]:
    total = len(pairs)
    passed = sum(1 for _, m in pairs if bool(m.get("passed")) is True)
    value = (passed / total) if total else None
    low, high = wilson_interval(passed, total) if total else (None, None)
    return {
        "value": value,
        "runs": total,
        "passed_runs": passed,
        "interval": [low, high] if total else None,
    }


def _slices(
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]], slice_by: List[str]
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for field in slice_by:
        groups: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
        for run, m in pairs:
            for key in _slice_keys(run, field):
                groups.setdefault(key, []).append((run, m))
        out[str(field)] = {key: _rate_block(g) for key, g in sorted(groups.items())}
    return out


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value]


def compute_metrics(
    runs: List[Dict[str, Any]],
    metrics_cfg: List[Dict[str, Any]],
    default_slice_by: Any = None,
) -> List[Dict[str, Any]]:
    """Aggregate measures into metrics.

    Every ``pass_rate`` carries a 95% Wilson ``interval``. A metric (or
    ``evaluation.defaults.slice_by``) may name scenario fields to ``slice_by``
    - ``expect.skin_tone``, ``tags`` - and the same rate is then also reported
    per value of each field under ``slices``. ``exclude_nonresponse: true``
    drops runs with no final answer from the denominator; by default they
    count as failures, and the ``answered_rate`` metric type reports how many
    there were.
    """
    per_measure: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for r in runs:
        for m in r.get("measures", []) or []:
            per_measure.setdefault(m.get("name"), []).append((r, m))
    default_slices = _as_list(default_slice_by)

    results: List[Dict[str, Any]] = []
    for mc in metrics_cfg or []:
        mtype = mc.get("type")
        name = mc.get("name") or mc.get("metric") or "metric"
        label = mc.get("label", name)
        res: Dict[str, Any] = {"name": name, "label": label, "type": mtype}
        slice_by = _as_list(mc.get("slice_by")) or default_slices

        if mtype == "pass_rate":
            src = str(
                mc.get("source_measure") or mc.get("source") or mc.get("measure") or ""
            )
            pairs = per_measure.get(src, [])
            excluded = 0
            if mc.get("exclude_nonresponse"):
                kept = [(r, m) for r, m in pairs if _answered(r)]
                excluded = len(pairs) - len(kept)
                pairs = kept
            res.update({"source_measure": src, **_rate_block(pairs)})
            if mc.get("exclude_nonresponse"):
                res["excluded_nonresponses"] = excluded
            if slice_by:
                res["slices"] = _slices(pairs, slice_by)
            target = mc.get("target")
            if target is not None:
                res["target"] = target
                meets = _apply_target(res["value"], target)
                res["meets_target"] = bool(meets) if meets is not None else False

        elif mtype == "answered_rate":
            pairs = [(r, {"passed": _answered(r)}) for r in runs]
            res.update(_rate_block(pairs))
            res["non_responses"] = res["runs"] - res["passed_runs"]
            if slice_by:
                res["slices"] = _slices(pairs, slice_by)
            target = mc.get("target")
            if target is not None:
                res["target"] = target
                meets = _apply_target(res["value"], target)
                res["meets_target"] = bool(meets) if meets is not None else False

        elif mtype == "rate":
            src = str(
                mc.get("from_measure")
                or mc.get("source_measure")
                or mc.get("measure")
                or ""
            )
            num_path = mc.get("numerator")
            den_path = mc.get("denominator")
            items = [m for _, m in per_measure.get(src, [])]
            num_sum = 0.0
            den_sum = 0.0
            for it in items:
                try:
                    n = _deep_get(it, num_path)
                    d = _deep_get(it, den_path)
                    if n is not None:
                        num_sum += float(n)
                    if d is not None:
                        den_sum += float(d)
                except Exception:
                    continue
            value = (num_sum / den_sum) if den_sum else None
            res.update(
                {
                    "source_measure": src,
                    "value": value,
                    "numerator_sum": num_sum,
                    "denominator_sum": den_sum,
                }
            )
            target = mc.get("target")
            if target is not None:
                res["target"] = target
                meets = _apply_target(value, target)
                res["meets_target"] = bool(meets) if meets is not None else False

        else:
            res.update({"value": None, "reason": f"unknown_metric_type:{mtype}"})

        results.append(res)

    return results
