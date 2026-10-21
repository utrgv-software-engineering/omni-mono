from pathlib import Path

import yaml

from omniagents.core.eval.split import create_splits


def test_create_splits_generates_labeled_outputs(tmp_path):
    scenarios = []
    for i in range(10):
        categories = ["safety"] if i % 2 == 0 else []
        scenarios.append(
            {
                "id": i,
                "name": f"scenario-{i}",
                "prompt": f"prompt-{i}",
                "failure_categories": categories,
            }
        )

    input_path = tmp_path / "scenarios.yml"
    input_path.write_text(
        yaml.dump({"scenarios": scenarios}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    first_dir = tmp_path / "split-one"
    second_dir = tmp_path / "split-two"

    first_paths = create_splits(
        input_path,
        "safety",
        output_dir=first_dir,
        f_train=0.2,
        f_dev=0.4,
        f_test=0.4,
        seed=7,
        negatives_ratio=0.6,
    )

    second_paths = create_splits(
        input_path,
        "safety",
        output_dir=second_dir,
        f_train=0.2,
        f_dev=0.4,
        f_test=0.4,
        seed=7,
        negatives_ratio=0.6,
    )

    def read_yaml(path: Path):
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    first_data = {key: read_yaml(path) for key, path in first_paths.items()}
    second_data = {key: read_yaml(path) for key, path in second_paths.items()}

    assert first_data == second_data

    combined = []
    for data in first_data.values():
        combined.extend(data.get("scenarios", []))

    positives = [sc for sc in combined if sc.get("label") is True]
    negatives = [sc for sc in combined if sc.get("label") is False]

    assert len(positives) == 5
    assert len(negatives) == 3
    assert all(sc.get("target_category") == "safety" for sc in combined)
