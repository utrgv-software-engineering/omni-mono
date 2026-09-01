from pathlib import Path

import pytest
import yaml

from omniagents.core.eval.holdout import (
    create_holdout,
    field_value,
    holdout_manifest,
    load_holdout,
    write_holdout,
)


def _scenarios(n=40):
    out = []
    for i in range(n):
        out.append(
            {
                "name": f"case-{i:03d}",
                "prompt": "hi",
                "tags": ["derm", "tone_56" if i % 2 else "tone_12"],
                "expect": {"skin_tone": "56" if i % 2 else "12", "malignant": i % 4 == 0},
            }
        )
    return out


def test_holdout_is_deterministic_for_a_seed():
    scenarios = _scenarios()
    a = create_holdout(scenarios, strata=["expect.skin_tone"], seed=7)
    b = create_holdout(scenarios, strata=["expect.skin_tone"], seed=7)
    c = create_holdout(scenarios, strata=["expect.skin_tone"], seed=8)
    assert a == b
    assert a != c
    assert set(a) == {s["name"] for s in scenarios}


def test_holdout_stratifies_every_stratum_by_the_fractions():
    scenarios = _scenarios(40)
    assignment = create_holdout(
        scenarios, strata=["expect.skin_tone", "expect.malignant"], fractions=(0.5, 0.25, 0.25), seed=1
    )
    manifest = holdout_manifest(assignment, scenarios, ["expect.skin_tone", "expect.malignant"], (0.5, 0.25, 0.25), 1)
    # strata are (12, malignant)=10, (12, benign)=10, (56, benign)=20 - odd i is never malignant
    assert manifest["total"] == 40 and manifest["counts"]["train"] == 20
    assert set(manifest["counts_by_stratum"]) == {"12/True", "12/False", "56/False"}
    # every stratum is split by the fractions to within largest-remainder rounding: never 10/0/0
    for stratum, counts in manifest["counts_by_stratum"].items():
        n = sum(counts.values())
        assert counts["train"] == n // 2, (stratum, counts)
        assert abs(counts["dev"] - counts["test"]) <= 1, (stratum, counts)


def test_holdout_rejects_bad_fractions_and_duplicate_names():
    with pytest.raises(ValueError):
        create_holdout(_scenarios(4), fractions=(0.5, 0.5, 0.5))
    dup = _scenarios(2)
    dup[1]["name"] = dup[0]["name"]
    with pytest.raises(ValueError):
        create_holdout(dup)


def test_field_value_joins_lists_and_reads_dotted_paths():
    sc = _scenarios(1)[0]
    assert field_value(sc, "expect.skin_tone") == "12"
    assert field_value(sc, "tags") == "derm|tone_12"
    assert field_value(sc, "missing.path") == ""


def test_write_and_load_holdout_roundtrip(tmp_path: Path):
    scenarios = _scenarios(12)
    assignment = create_holdout(scenarios, strata=["expect.skin_tone"], seed=3)
    manifest = holdout_manifest(assignment, scenarios, ["expect.skin_tone"], (0.5, 0.25, 0.25), 3)
    paths = write_holdout(tmp_path / "evaluations", assignment, manifest)
    assert paths["test"] == tmp_path / "evaluations" / "splits" / "eval" / "test.yml"
    names, loaded_manifest = load_holdout(tmp_path / "evaluations", "test")
    assert names == sorted(k for k, s in assignment.items() if s == "test")
    assert loaded_manifest["seed"] == 3
    # the files hold names only, never scenario bodies
    data = yaml.safe_load(paths["train"].read_text())
    assert set(data) == {"split", "scenarios"}
    with pytest.raises(FileNotFoundError):
        load_holdout(tmp_path / "nowhere", "test")
    with pytest.raises(ValueError):
        load_holdout(tmp_path / "evaluations", "validation")
