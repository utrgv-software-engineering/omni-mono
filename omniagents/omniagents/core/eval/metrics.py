from __future__ import annotations

from typing import Any, Dict, List, Optional


def _deep_get(d: Any, path: str) -> Any:
    cur = d
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _apply_target(value: Optional[float], target: Dict[str, Any] | None) -> Optional[bool]:
    if value is None or not target:
        return None
    op = str(target.get("op", ">=")).strip()
    try:
        tgt = float(target.get("value"))
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


def compute_metrics(runs: List[Dict[str, Any]], metrics_cfg: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    per_measure: Dict[str, List[Dict[str, Any]]] = {}
    for r in runs:
        for m in r.get("measures", []) or []:
            per_measure.setdefault(m.get("name"), []).append(m)

    results: List[Dict[str, Any]] = []
    for mc in metrics_cfg or []:
        mtype = mc.get("type")
        name = mc.get("name") or mc.get("metric") or "metric"
        label = mc.get("label", name)
        res: Dict[str, Any] = {"name": name, "label": label, "type": mtype}

        if mtype == "pass_rate":
            src = mc.get("source_measure") or mc.get("source") or mc.get("measure")
            items = per_measure.get(src, [])
            total = len(items)
            passed = sum(1 for it in items if bool(it.get("passed")) is True)
            value = (passed / total) if total else None
            res.update({
                "source_measure": src,
                "value": value,
                "runs": total,
                "passed_runs": passed,
            })
            target = mc.get("target")
            if target is not None:
                res["target"] = target
                meets = _apply_target(value, target)
                res["meets_target"] = bool(meets) if meets is not None else False

        elif mtype == "rate":
            src = mc.get("from_measure") or mc.get("source_measure") or mc.get("measure")
            num_path = mc.get("numerator")
            den_path = mc.get("denominator")
            items = per_measure.get(src, [])
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
            res.update({
                "source_measure": src,
                "value": value,
                "numerator_sum": num_sum,
                "denominator_sum": den_sum,
            })
            target = mc.get("target")
            if target is not None:
                res["target"] = target
                meets = _apply_target(value, target)
                res["meets_target"] = bool(meets) if meets is not None else False

        else:
            res.update({"value": None, "reason": f"unknown_metric_type:{mtype}"})

        results.append(res)

    return results

