"""Student data reporting utilities."""

import csv
from pathlib import Path


def load_students(csv_path: str) -> list[dict]:
    with open(csv_path) as f:
        return list(csv.DictReader(f))


def students_on_probation(students: list[dict]) -> list[dict]:
    return [s for s in students if s.get("academic_standing") == "Academic Probation"]


def average_gpa_by_major(students: list[dict]) -> dict[str, float]:
    by_major: dict[str, list[float]] = {}
    for s in students:
        major = s.get("major", "Unknown")
        try:
            by_major.setdefault(major, []).append(float(s["gpa"]))
        except (ValueError, KeyError):
            continue
    return {m: sum(gs) / len(gs) for m, gs in by_major.items()}
