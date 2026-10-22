from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

AUTH_HEADER = {"Authorization": "Bearer secret"}


def _project_dir(studio_api_env) -> Path:
    manifest = studio_api_env.app.state.playground_resources["manifest_path"]
    return Path(manifest).parent


def _assert_timestamp(value: str) -> None:
    assert value.endswith("Z")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo == timezone.utc


def _create_dataset(project_dir: Path, base_ts: int) -> dict[str, str]:
    set_dir = project_dir / "artifacts" / "eval" / "temp-set"
    set_dir.mkdir(parents=True, exist_ok=True)
    scenario_payload = {"scenarios": [{"name": "one", "prompt": "hi"}]}
    generated_path = set_dir / "generated_scenarios.yml"
    generated_path.write_text(
        yaml.safe_dump({"scenarios": []}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    os.utime(generated_path, (base_ts, base_ts))
    splits_dir = set_dir / "splits" / "category"
    splits_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for index, split in enumerate(("train", "dev", "test")):
        split_path = splits_dir / f"{split}.yml"
        split_path.write_text(
            yaml.safe_dump(scenario_payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        timestamp = base_ts + index + 1
        os.utime(split_path, (timestamp, timestamp))
        paths[split] = str(split_path)
    return paths


def test_evaluation_scenarios_timestamps_are_timezone_aware(studio_api_env):
    project_dir = _project_dir(studio_api_env)
    golden_path = project_dir / "evaluations" / "scenarios.yml"
    generated_path = (
        project_dir / "artifacts" / "eval" / "demo-set" / "generated_scenarios.yml"
    )
    base_ts = 1710000000
    os.utime(golden_path, (base_ts, base_ts))
    os.utime(generated_path, (base_ts + 10, base_ts + 10))
    response = studio_api_env.client.get(
        "/api/evaluation/scenarios", headers=AUTH_HEADER
    )
    assert response.status_code == 200
    payload = response.json()
    for key in ("golden", "generated", "annotated"):
        for entry in payload.get(key, []):
            value = entry.get("updated_at")
            if value is None:
                continue
            _assert_timestamp(value)


def test_evaluation_datasets_timestamps_are_timezone_aware(studio_api_env):
    project_dir = _project_dir(studio_api_env)
    paths = _create_dataset(project_dir, 1720000000)
    response = studio_api_env.client.get(
        "/api/evaluation/datasets", headers=AUTH_HEADER
    )
    assert response.status_code == 200
    data = response.json()
    target = None
    for item in data.get("datasets", []):
        if item.get("set_name") == "temp-set":
            target = item
            break
    assert target is not None
    for split in target.get("splits", []):
        value = split.get("updated_at")
        if value is None:
            continue
        _assert_timestamp(value)


def test_evaluation_dataset_split_timestamp_is_timezone_aware(studio_api_env):
    project_dir = _project_dir(studio_api_env)
    paths = _create_dataset(project_dir, 1730000000)
    response = studio_api_env.client.get(
        "/api/evaluation/datasets/split",
        params={"path": paths["dev"]},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 200
    data = response.json()
    value = data["split"]["updated_at"]
    _assert_timestamp(value)
