"""Where a project's evaluation keeps each thing, in one place.

Everything lives under the evaluations directory (``evaluations/`` unless
``project.yml`` names another with ``paths.evaluations``). What people
write sits apart from what the tools produce:

Written by people, committed::

    evaluation.yml              settings: defaults, environment, dimensions,
                                failure categories, measures, metrics
    measures.py                 the project's measure functions
    judges/<key>/judge.yml      a judge, beside its instructions and template
    optimizers/<key>/optimizer.yml
    scenarios/**/*.yml          the scenarios a suite runs

Produced by people and tools, committed::

    reviews/<run id>.jsonl      people's verdicts on a run's answers, each
                                with the answer it judges
    generated/<set>/            a generated set: the combinations of dimension
                                values a person reviewed (combinations.yml),
                                its scenarios.yml and manifest.json, and the
                                queries the realism filter left out
                                (regenerating costs model calls and never
                                gives the same set)
    holdout/                    the suite's train/dev/test assignment
    judge-datasets/<category>/  a category's reviewed answers, split into
                                train/dev/test for its judge
    baselines/<name>/           a run kept as evidence for a number reported

Local results, kept out of the repository by the scaffolded ``.gitignore``::

    runs/                       every saved run: index.json, <run id>/
    optimizations/<id>/         agent and judge optimizations

What is committed is everything expensive or impossible to make again, and
everything a reported number depends on. It includes agents' answers, so
evaluation inputs must be synthetic.

Nothing is read from anywhere else. A project laid out the old way is
refused by :func:`check_layout` with what to move where, rather than half
loaded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

EVALUATION_FILE = "evaluation.yml"
MEASURES_FILE = "measures.py"
JUDGES = "judges"
JUDGE_FILE = "judge.yml"
OPTIMIZERS = "optimizers"
OPTIMIZER_FILE = "optimizer.yml"
SCENARIOS = "scenarios"
GENERATED = "generated"
GENERATED_SCENARIOS = "scenarios.yml"
GENERATED_MANIFEST = "manifest.json"
COMBINATIONS_FILE = "combinations.yml"
HOLDOUT = "holdout"
JUDGE_DATASETS = "judge-datasets"
DATASET_MANIFEST = "manifest.yml"
RUNS = "runs"
RUNS_INDEX = "index.json"
OPTIMIZATIONS = "optimizations"
REVIEWS = "reviews"
BASELINES = "baselines"
SPLIT_NAMES = ("train", "dev", "test")


def slugify(value: str) -> str:
    """A failure category's id: its name, lowercased, words joined by ``_``."""
    token = re.sub(r"[^a-z0-9\-_]+", "_", (value or "").strip().lower())
    return re.sub(r"_+", "_", token).strip("_")


class EvalLayoutError(ValueError):
    """The evaluations directory holds files where nothing reads them."""


def evaluations_dir(project_root: Path, project_config: Dict[str, Any]) -> Path:
    """The project's evaluations directory: ``paths.evaluations`` in
    ``project.yml`` (the first, when it lists several), else
    ``evaluations/``."""
    configured = (project_config.get("paths") or {}).get("evaluations")
    if isinstance(configured, list):
        configured = configured[0] if configured else None
    return (Path(project_root) / str(configured or "evaluations")).resolve()


class EvalLayout:
    """The paths of one evaluations directory."""

    def __init__(self, eval_dir: Path, project_root: Path) -> None:
        self.eval_dir = Path(eval_dir)
        self.project_root = Path(project_root)

    @property
    def evaluation_file(self) -> Path:
        return self.eval_dir / EVALUATION_FILE

    @property
    def measures_file(self) -> Path:
        return self.eval_dir / MEASURES_FILE

    @property
    def judges_dir(self) -> Path:
        return self.eval_dir / JUDGES

    def judge_dir(self, key: str) -> Path:
        return self.judges_dir / key

    @property
    def optimizers_dir(self) -> Path:
        return self.eval_dir / OPTIMIZERS

    @property
    def scenarios_dir(self) -> Path:
        return self.eval_dir / SCENARIOS

    @property
    def benchmarks_dir(self) -> Path:
        """Benchmark suites: tasks taken from real runs, run across configurations
        (``eval scenarios run --set <suite> --matrix``), never as the suite."""
        return self.eval_dir / "benchmarks"

    def benchmark_files(self) -> List[Path]:
        if not self.benchmarks_dir.is_dir():
            return []
        return [p.resolve() for p in sorted(self.benchmarks_dir.glob("*.yml"))]

    def scenario_files(self) -> List[Path]:
        """The files whose scenarios a suite runs."""
        if not self.scenarios_dir.is_dir():
            return []
        return [p.resolve() for p in sorted(self.scenarios_dir.rglob("*.yml"))]

    @property
    def generated_dir(self) -> Path:
        return self.eval_dir / GENERATED

    def generated_set_dir(self, name: str) -> Path:
        return self.generated_dir / name

    def generated_sets(self) -> List[Path]:
        """Each generated set's folder."""
        if not self.generated_dir.is_dir():
            return []
        return [
            entry
            for entry in sorted(self.generated_dir.iterdir())
            if (entry / GENERATED_SCENARIOS).is_file()
        ]

    @property
    def holdout_dir(self) -> Path:
        return self.eval_dir / HOLDOUT

    @property
    def judge_datasets_dir(self) -> Path:
        return self.eval_dir / JUDGE_DATASETS

    def judge_dataset_dir(self, category: str) -> Path:
        return self.judge_datasets_dir / category

    @property
    def runs_dir(self) -> Path:
        return self.eval_dir / RUNS

    @property
    def runs_index(self) -> Path:
        return self.runs_dir / RUNS_INDEX

    @property
    def optimizations_dir(self) -> Path:
        return self.eval_dir / OPTIMIZATIONS

    @property
    def reviews_dir(self) -> Path:
        return self.eval_dir / REVIEWS

    def review_file(self, run_id: str) -> Path:
        return self.reviews_dir / f"{run_id}.jsonl"

    @property
    def baselines_dir(self) -> Path:
        return self.eval_dir / BASELINES

    @property
    def review_assist_dir(self) -> Path:
        """The review assistant's working state (sample, proposals,
        suggestions): local, under ``runs/``."""
        return self.runs_dir / "review-assist"

    def relative(self, path: Path) -> str:
        """``path`` as written in records: relative to the evaluations
        directory when inside it."""
        resolved = Path(path).resolve()
        try:
            return str(resolved.relative_to(self.eval_dir.resolve()))
        except ValueError:
            return str(resolved)

    def resolve(self, recorded: str) -> Path:
        """A path written by :meth:`relative`, absolute again."""
        path = Path(recorded)
        return path if path.is_absolute() else (self.eval_dir / path).resolve()


def _evaluation_settings(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("evaluation")
    return block if isinstance(block, dict) else data


def layout_problems(layout: EvalLayout) -> List[str]:
    """Files in the old places, each with where it goes now."""
    eval_dir = layout.eval_dir
    problems: List[str] = []

    def moved(old: Path, new: str) -> None:
        if old.exists():
            problems.append(f"{old} is no longer read: {new}")

    moved(eval_dir / "scenarios.yml", "move its scenarios into scenarios/<name>.yml")
    moved(
        eval_dir / "metrics.yml", "move its metrics under `metrics:` in evaluation.yml"
    )
    moved(eval_dir / "metrics", "move its metrics under `metrics:` in evaluation.yml")
    moved(eval_dir / "splits" / "eval", "the holdout is holdout/; make it again")
    moved(eval_dir / "results", "runs are saved in runs/")
    moved(eval_dir / "agent_optimize", "optimizations are saved in optimizations/")
    moved(
        layout.project_root / "artifacts" / "eval",
        "generated sets are evaluations/generated/<set>/scenarios.yml, judge "
        "datasets evaluations/judge-datasets/<category>/",
    )
    if layout.judge_datasets_dir.is_dir():
        for manifest in sorted(layout.judge_datasets_dir.glob(f"*/{DATASET_MANIFEST}")):
            try:
                data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
            if not isinstance(data, dict) or data.get("source") != "reviews":
                problems.append(
                    f"{manifest.parent} was built from scenario labels; judge "
                    "datasets are built from reviews: delete it and build it again"
                )
    settings = _evaluation_settings(layout.evaluation_file)
    if settings.get("judges"):
        problems.append(
            f"{layout.evaluation_file} declares judges: give each its own "
            "judges/<key>/judge.yml"
        )
    if settings.get("scenarios"):
        problems.append(
            f"{layout.evaluation_file} lists scenarios: move them into "
            "scenarios/<name>.yml"
        )
    if settings.get("optimizers"):
        problems.append(
            f"{layout.evaluation_file} declares optimizers: give each its own "
            "optimizers/<key>/optimizer.yml"
        )
    for folder, filename, kind in (
        (layout.judges_dir, JUDGE_FILE, "judge"),
        (layout.optimizers_dir, OPTIMIZER_FILE, "optimizer"),
    ):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yml")):
            problems.append(
                f"{path} is not read: a {kind} is {folder.name}/<key>/{filename}"
            )
        for path in sorted(folder.glob(f"*/{filename}")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                problems.append(f"{path} does not parse: {exc}")
                continue
            if isinstance(data, dict) and isinstance(
                data.get(f"{kind}s"), (dict, list)
            ):
                problems.append(
                    f"{path} wraps the {kind} in `{kind}s:`; the file is the "
                    f"{kind} itself, named by its folder"
                )
    return problems


def check_layout(layout: EvalLayout) -> None:
    """Refuse an evaluations directory with files in the old places."""
    problems = layout_problems(layout)
    if problems:
        raise EvalLayoutError(
            "The evaluations directory is laid out the old way:\n- "
            + "\n- ".join(problems)
        )
