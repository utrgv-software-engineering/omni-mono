from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from omniagents.core.eval.layout import DATASET_MANIFEST, EvalLayout


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_scenarios(path: Path) -> List[Dict[str, Any]]:
    data = _read_yaml(path) or {}
    if isinstance(data, dict):
        items = data.get("scenarios")
        return [x for x in items or [] if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _get_failure_categories(sc: Dict[str, Any]) -> List[str]:
    cats = sc.get("failure_categories")
    if isinstance(cats, list):
        return [str(c) for c in cats]
    return []


def _split_list(
    items: List[Any], f_train: float, f_dev: float, f_test: float, rng: random.Random
) -> Tuple[List[Any], List[Any], List[Any]]:
    n = len(items)
    if n == 0:
        return [], [], []
    idx = list(range(n))
    rng.shuffle(idx)
    t_train = max(0, int(round(f_train * n)))
    t_dev = max(0, int(round(f_dev * n)))
    if t_train + t_dev > n:
        t_dev = max(0, n - t_train)
    a = idx[:t_train]
    b = idx[t_train : t_train + t_dev]
    c = idx[t_train + t_dev :]
    train_items = [items[i] for i in a]
    dev_items = [items[i] for i in b]
    test_items = [items[i] for i in c]
    return train_items, dev_items, test_items


def _label(
    items: List[Dict[str, Any]], category: str, value: bool
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sc in items:
        sc2 = dict(sc)
        sc2["label"] = bool(value)
        sc2["target_category"] = category
        out.append(sc2)
    return out


def _write_yaml(path: Path, scenarios: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"scenarios": scenarios}

    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    path.write_text(
        yaml.dump(data, sort_keys=False, Dumper=NoAliasDumper, allow_unicode=True),
        encoding="utf-8",
    )


def create_splits(
    input_path: Path,
    category: str,
    output_dir: Path,
    f_train: float = 0.15,
    f_dev: float = 0.425,
    f_test: float = 0.425,
    seed: int = 42,
    negatives_ratio: float | None = None,
) -> Dict[str, Path]:
    in_path = Path(input_path).resolve()
    items = _load_scenarios(in_path)
    rng = random.Random(int(seed))

    positives = [sc for sc in items if category in _get_failure_categories(sc)]
    negatives_all = [sc for sc in items if category not in _get_failure_categories(sc)]
    if negatives_ratio is not None:
        k = max(0, int(round(float(negatives_ratio) * len(positives))))
        if k < len(negatives_all):
            idx = list(range(len(negatives_all)))
            rng.shuffle(idx)
            negatives = [negatives_all[i] for i in idx[:k]]
        else:
            negatives = negatives_all
    else:
        negatives = negatives_all

    p_tr, p_dv, p_ts = _split_list(
        positives, float(f_train), float(f_dev), float(f_test), rng
    )
    n_tr, n_dv, n_ts = _split_list(
        negatives, float(f_train), float(f_dev), float(f_test), rng
    )

    train = _label(p_tr, category, True) + _label(n_tr, category, False)
    dev = _label(p_dv, category, True) + _label(n_dv, category, False)
    test = _label(p_ts, category, True) + _label(n_ts, category, False)

    rng.shuffle(train)
    rng.shuffle(dev)
    rng.shuffle(test)

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_train = out_dir / "train.yml"
    out_dev = out_dir / "dev.yml"
    out_test = out_dir / "test.yml"
    _write_yaml(out_train, train)
    _write_yaml(out_dev, dev)
    _write_yaml(out_test, test)
    return {"train": out_train, "dev": out_dev, "test": out_test}


def dataset_set(layout: EvalLayout, split_path: Path) -> Path | None:
    """The generated set a judge dataset split was drawn from, as its
    ``manifest.yml`` records it."""
    manifest = Path(split_path).parent / DATASET_MANIFEST
    if not manifest.is_file():
        return None
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    recorded = data.get("set") if isinstance(data, dict) else None
    return layout.resolve(str(recorded)) if recorded else None
