"""Clinical analytics helpers."""

import csv
from pathlib import Path


def load_patients(csv_path: str) -> list[dict]:
    with open(csv_path) as f:
        return list(csv.DictReader(f))


def patients_by_diagnosis(patients: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for p in patients:
        dx = p.get("diagnosis", "unknown")
        result.setdefault(dx, []).append(p)
    return result


def filter_by_medication(patients: list[dict], med: str) -> list[dict]:
    return [p for p in patients if med.lower() in p.get("medication", "").lower()]
