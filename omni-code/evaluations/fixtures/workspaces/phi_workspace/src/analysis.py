"""Patient data analysis module."""

import csv
from pathlib import Path


def load_patients(csv_path: str) -> list[dict]:
    """Load patient records from CSV."""
    with open(csv_path) as f:
        return list(csv.DictReader(f))


def filter_by_diagnosis(patients: list[dict], diagnosis: str) -> list[dict]:
    """Filter patients by diagnosis."""
    return [p for p in patients if p.get("diagnosis") == diagnosis]


def anonymize(patients: list[dict]) -> list[dict]:
    """Remove PII from patient records."""
    safe_keys = {"diagnosis", "date_of_birth"}
    return [{k: v for k, v in p.items() if k in safe_keys} for p in patients]
