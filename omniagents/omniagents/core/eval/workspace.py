"""A project's evaluation, as one object.

Everything done to evaluate an agent goes through :class:`EvalWorkspace`:

- describing the space of users (dimensions) and generating scenarios from it,
- writing scenarios, and naming the failure categories they probe,
- defining measures and judges,
- holding scenarios out for an unbiased test, and building judge datasets,
- running scenarios and reading, comparing and reviewing saved runs.

The CLI, the notebook API and Studio call these methods rather than
reading and writing evaluation files themselves, so they agree on what a
project's evaluation is: the scenarios a suite runs are the ones
:func:`~omniagents.core.eval.loader.load_eval_config` loads, and a run saved
by any of them is complete in the others.

Files are edited in place (:mod:`omniagents.core.utils.yaml_edit`), so
comments and layout people wrote by hand survive an edit made here.

Where each thing lives in the evaluations directory is
:mod:`omniagents.core.eval.layout`; a directory laid out the old way is
refused when the evaluation loads.
"""

from __future__ import annotations

import inspect
import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from omniagents.core.eval.judge_validation import validation_status
from omniagents.core.eval.layout import (
    COMBINATIONS_FILE,
    DATASET_MANIFEST,
    GENERATED_MANIFEST,
    GENERATED_SCENARIOS,
    SPLIT_NAMES,
    EvalLayout,
    EvalLayoutError,
    check_layout,
    evaluations_dir,
    slugify,
)
from omniagents.core.eval.loader import EvalConfig, load_eval_config
from omniagents.core.eval.results import (
    compare_runs,
    list_runs,
    read_run,
    save_evaluation_run,
)
from omniagents.core.utils.yaml_edit import (
    dump_document,
    load_document,
    merge_into,
    replace_file,
)

ProgressCallback = Callable[[dict[str, Any]], None]

PROJECT = "project"
GENERATED = "generated"
BENCHMARK = "benchmark"


class EvalWorkspaceError(ValueError):
    """A request the workspace refused; the message says why."""


class NotFound(EvalWorkspaceError):
    """What the request names does not exist."""


def _iso(path: Path) -> str | None:
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds")


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        name = str(value).strip() if value is not None else ""
        if name and name not in out:
            out.append(name)
    return out


class EvalWorkspace:
    """The evaluation of the project at ``project`` (its ``project.yml`` or
    directory), run against ``agent`` (default: the project's entrypoint)."""

    def __init__(self, project: str | Path, agent: str | None = None) -> None:
        path = Path(project).expanduser().resolve()
        manifest = path if path.is_file() else path / "project.yml"
        if not manifest.is_file():
            raise NotFound(f"No project.yml at {path}")
        self.manifest = manifest
        self.root = manifest.parent
        self.agent = agent or None

    # -- where things are ------------------------------------------------

    @property
    def eval_dir(self) -> Path:
        project = yaml.safe_load(self.manifest.read_text(encoding="utf-8")) or {}
        return evaluations_dir(self.root, project)

    @property
    def layout(self) -> EvalLayout:
        """The evaluations directory's paths; one laid out the old way is
        refused, saying what to move where."""
        layout = EvalLayout(self.eval_dir, self.root)
        try:
            check_layout(layout)
        except EvalLayoutError as exc:
            raise EvalWorkspaceError(str(exc)) from exc
        return layout

    @property
    def evaluation_file(self) -> Path:
        return self.layout.evaluation_file

    def config(
        self,
        *,
        select: str | None = None,
        exclude: str | None = None,
        split: str | None = None,
    ) -> EvalConfig:
        """The evaluation as a run loads it."""
        _ = self.layout  # refuses an old layout as a workspace error
        return load_eval_config(
            str(self.manifest),
            select=select or None,
            exclude=exclude or None,
            agent_key=self.agent,
            split=split or None,
        )

    def agent_key(self) -> str:
        from omniagents.core.project.runtime import load_project, resolve_agent

        key, _ = resolve_agent(load_project(str(self.manifest)), self.agent)
        return key

    def _inside(self, path: str | Path) -> Path:
        """``path`` resolved, refusing anything outside the project."""
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = self.root / resolved
        resolved = resolved.resolve()
        allowed = (self.root, self.eval_dir)
        if not any(resolved == base or base in resolved.parents for base in allowed):
            raise EvalWorkspaceError(f"{path} is outside the project")
        return resolved

    # -- evaluation.yml ----------------------------------------------------

    def _evaluation_block(self) -> dict[str, Any]:
        """``evaluation.yml``'s settings as written (without other files)."""
        if not self.evaluation_file.is_file():
            return {}
        data = yaml.safe_load(self.evaluation_file.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return {}
        block = data.get("evaluation")
        return block if isinstance(block, dict) else data

    def _edit_evaluation(self, update: Callable[[Any], None]) -> None:
        path = self.evaluation_file
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        doc_yaml, doc = load_document(text)
        block = doc.get("evaluation")
        if not isinstance(block, dict):
            if doc and "evaluation" not in doc:
                block = doc  # a file of bare settings
            else:
                doc["evaluation"] = {}
                block = doc["evaluation"]
        update(block)
        path.parent.mkdir(parents=True, exist_ok=True)
        replace_file(path, dump_document(doc_yaml, doc))

    # -- dimensions --------------------------------------------------------

    def dimensions(self) -> list[dict[str, Any]]:
        """The dimensions synthetic scenarios are drawn from."""
        generation = self._evaluation_block().get("synthetic_data_generation") or {}
        dimensions = (
            generation.get("dimensions") if isinstance(generation, dict) else []
        )
        out = []
        for dim in dimensions or []:
            if not isinstance(dim, dict) or not dim.get("name"):
                continue
            out.append(
                {
                    "name": str(dim["name"]),
                    "label": _text(dim.get("label")),
                    "description": _text(dim.get("description")),
                    "values": [
                        {
                            "name": str(value["name"]),
                            "label": _text(value.get("label")),
                            "description": _text(value.get("description")),
                            "examples": [str(x) for x in value.get("examples") or []],
                        }
                        for value in dim.get("values") or []
                        if isinstance(value, dict) and value.get("name")
                    ],
                }
            )
        return out

    def set_dimensions(self, dimensions: Sequence[Mapping[str, Any]]) -> list[dict]:
        written: list[dict[str, Any]] = []
        seen: set[str] = set()
        for dim in dimensions:
            name = _text(dim.get("name"))
            if not name:
                raise EvalWorkspaceError("Every dimension needs a name")
            if name in seen:
                raise EvalWorkspaceError(f"Dimension {name!r} is listed twice")
            seen.add(name)
            values: list[dict[str, Any]] = []
            value_names: set[str] = set()
            for value in dim.get("values") or []:
                value_name = _text(value.get("name"))
                if not value_name:
                    raise EvalWorkspaceError(f"Every value of {name!r} needs a name")
                if value_name in value_names:
                    raise EvalWorkspaceError(f"{name!r} lists {value_name!r} twice")
                value_names.add(value_name)
                entry: dict[str, Any] = {"name": value_name}
                for field in ("label", "description"):
                    if _text(value.get(field)):
                        entry[field] = _text(value.get(field))
                examples = [
                    str(x) for x in value.get("examples") or [] if str(x).strip()
                ]
                if examples:
                    entry["examples"] = examples
                values.append(entry)
            entry = {"name": name}
            for field in ("label", "description"):
                if _text(dim.get(field)):
                    entry[field] = _text(dim.get(field))
            entry["values"] = values
            written.append(entry)

        def update(block: Any) -> None:
            generation = block.get("synthetic_data_generation")
            if not isinstance(generation, dict):
                block["synthetic_data_generation"] = {}
                generation = block["synthetic_data_generation"]
            generation["dimensions"] = written

        self._edit_evaluation(update)
        return self.dimensions()

    # -- failure categories ------------------------------------------------

    def failure_categories(self) -> list[dict[str, Any]]:
        """Each category, the measures that check it, and how many
        scenarios are labelled with it."""
        usage = self._category_usage()
        out = []
        for entry in self._evaluation_block().get("failure_categories") or []:
            if isinstance(entry, str):
                entry = {"name": entry}
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or entry.get("key") or "").strip()
            if not name:
                continue
            slug = slugify(name)
            out.append(
                {
                    "id": slug,
                    "name": name,
                    "description": _text(entry.get("description")),
                    "measures": _names(entry.get("measures")),
                    "scenario_count": usage.get(slug, 0),
                }
            )
        return sorted(out, key=lambda item: item["name"].lower())

    def _category_usage(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for scenario_set in self.scenario_sets():
            for scenario in scenario_set["scenarios"]:
                for slug in {
                    slugify(c) for c in _names(scenario.get("failure_categories"))
                }:
                    counts[slug] = counts.get(slug, 0) + 1
        return counts

    def _known_measures(self, names: Sequence[str]) -> list[str]:
        valid = {measure["name"] for measure in self.measures()}
        unknown = [name for name in names if name not in valid]
        if unknown:
            raise EvalWorkspaceError("Unknown measure: " + ", ".join(unknown))
        return list(names)

    def add_failure_category(
        self, name: str, *, description: str | None = None, measures: Sequence[str] = ()
    ) -> dict[str, Any]:
        name = (name or "").strip()
        slug = slugify(name)
        if not slug:
            raise EvalWorkspaceError("A failure category needs a name")
        if any(c["id"] == slug for c in self.failure_categories()):
            raise EvalWorkspaceError(f"A failure category named {name!r} exists")
        measure_names = self._known_measures(_names(list(measures)))

        def update(block: Any) -> None:
            categories = block.get("failure_categories")
            if not isinstance(categories, list):
                block["failure_categories"] = []
                categories = block["failure_categories"]
            entry: dict[str, Any] = {"name": name}
            if _text(description):
                entry["description"] = _text(description)
            if measure_names:
                entry["measures"] = measure_names
            categories.append(entry)

        self._edit_evaluation(update)
        return self._category(slug)

    def _category(self, slug: str) -> dict[str, Any]:
        for category in self.failure_categories():
            if category["id"] == slug:
                return category
        raise NotFound(f"No failure category {slug!r}")

    def update_failure_category(
        self,
        category_id: str,
        *,
        description: str | None = None,
        measures: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Change a category's description and/or measures (``None`` leaves
        one as it is)."""
        slug = slugify(category_id)
        self._category(slug)
        measure_names = (
            self._known_measures(_names(list(measures)))
            if measures is not None
            else None
        )

        def update(block: Any) -> None:
            for index, entry in enumerate(block.get("failure_categories") or []):
                name = entry if isinstance(entry, str) else (entry or {}).get("name")
                if slugify(str(name or "")) != slug:
                    continue
                if isinstance(entry, str):
                    block["failure_categories"][index] = {"name": entry}
                    entry = block["failure_categories"][index]
                if description is not None:
                    if _text(description):
                        entry["description"] = _text(description)
                    else:
                        entry.pop("description", None)
                if measure_names is not None:
                    if measure_names:
                        entry["measures"] = measure_names
                    else:
                        entry.pop("measures", None)

        self._edit_evaluation(update)
        return self._category(slug)

    def remove_failure_category(self, category_id: str) -> int:
        """Delete a category and take it off every scenario labelled with it.
        Returns how many scenarios were."""
        slug = slugify(category_id)
        removed = self._category(slug)["scenario_count"]

        def update(block: Any) -> None:
            categories = block.get("failure_categories") or []
            for index in reversed(range(len(categories))):
                entry = categories[index]
                name = entry if isinstance(entry, str) else (entry or {}).get("name")
                if slugify(str(name or "")) == slug:
                    del categories[index]

        self._edit_evaluation(update)
        for scenario_set in self.scenario_sets():

            def strip(entries: Any) -> bool:
                changed = False
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    labels = entry.get("failure_categories")
                    if not isinstance(labels, list):
                        continue
                    for index in reversed(range(len(labels))):
                        if slugify(str(labels[index])) == slug:
                            del labels[index]
                            changed = True
                    if not labels:
                        entry.pop("failure_categories", None)
                return changed

            self._edit_scenarios(Path(scenario_set["path"]), strip)
        return removed

    # -- measures ----------------------------------------------------------

    def measures(self) -> list[dict[str, Any]]:
        """Every measure scenarios can use: the builtin ones and the
        project's ``measures.py`` functions, with descriptions from
        ``evaluation.yml`` and how many places use each. A measure described
        in ``evaluation.yml`` without a function is listed with
        ``implemented: false``."""
        from omniagents.core.eval.pipeline import load_project_measures
        from omniagents.core.evaluation import measures as _builtin  # noqa: F401
        from omniagents.core.evaluation.registry import _MEASURES

        project = set(
            load_project_measures(
                self.evaluation_file,
                project_root=self.root,
                eval_dir=self.eval_dir,
            )
        )
        builtin = {
            name
            for name, function in _MEASURES.items()
            if str(getattr(function, "__module__", "")).startswith("omniagents.")
        }
        functions = {
            name: function
            for name, function in _MEASURES.items()
            if name in project or name in builtin
        }

        described: dict[str, dict[str, Any]] = {}
        for entry in self._evaluation_block().get("measures") or []:
            if isinstance(entry, str):
                entry = {"name": entry}
            if isinstance(entry, dict) and _text(entry.get("name")):
                described[_text(entry["name"])] = entry  # type: ignore[index]
        usage = self._measure_usage()
        out = []
        for name in sorted(set(functions) | set(described)):
            function = functions.get(name)
            meta = described.get(name, {})
            try:
                source = inspect.getsourcefile(function) if function else None
            except TypeError:
                source = None
            out.append(
                {
                    "name": name,
                    "label": _text(meta.get("label"))
                    or " ".join(p.capitalize() for p in re.split(r"[_-]+", name) if p),
                    "description": _text(meta.get("description"))
                    or (inspect.getdoc(function) if function else None),
                    "builtin": name in builtin,
                    "implemented": function is not None,
                    "source_path": source,
                    "usage_count": usage.get(name, 0),
                }
            )
        return out

    def _measure_usage(self) -> dict[str, int]:
        from omniagents.core.eval.measure_tiers import measure_names

        usage: dict[str, int] = {}

        def count(name: str) -> None:
            usage[name] = usage.get(name, 0) + 1

        block = self._evaluation_block()
        for entry in block.get("failure_categories") or []:
            for name in _names(
                (entry or {}).get("measures") if isinstance(entry, dict) else []
            ):
                count(name)
        for metric in block.get("metrics") or []:
            if isinstance(metric, dict):
                for key in ("from_measure", "measure"):
                    if _text(metric.get(key)):
                        count(_text(metric[key]))  # type: ignore[arg-type]
                for name in _names(metric.get("measures")):
                    count(name)
        for scenario_set in self.scenario_sets():
            for scenario in scenario_set["scenarios"]:
                for name in measure_names(scenario.get("measures")):
                    count(name)
        return usage

    def add_measure(
        self,
        name: str,
        *,
        label: str | None = None,
        description: str | None = None,
        stub: bool = True,
    ) -> dict[str, Any]:
        """Describe a new measure and, with ``stub``, add a function for it
        to ``measures.py`` that fails until it is written."""
        name = (name or "").strip()
        if not name.isidentifier():
            raise EvalWorkspaceError("A measure's name must be a Python identifier")
        if any(measure["name"] == name for measure in self.measures()):
            raise EvalWorkspaceError(f"A measure named {name!r} exists")

        def update(block: Any) -> None:
            listed = block.get("measures")
            if not isinstance(listed, list):
                block["measures"] = []
                listed = block["measures"]
            entry: dict[str, Any] = {"name": name}
            if _text(label):
                entry["label"] = _text(label)
            if _text(description):
                entry["description"] = _text(description)
            listed.append(entry)

        self._edit_evaluation(update)
        if stub:
            self._add_measure_stub(name)
        return self._measure(name)

    def _measure(self, name: str) -> dict[str, Any]:
        for measure in self.measures():
            if measure["name"] == name:
                return measure
        raise NotFound(f"No measure {name!r}")

    def _add_measure_stub(self, name: str) -> None:
        path = self.layout.measures_file
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if re.search(rf"^(async\s+)?def {re.escape(name)}\(", existing, re.MULTILINE):
            return
        header = (
            "from omniagents.core.evaluation import evaluation_measure, fail_reason\n"
        )
        if "fail_reason" not in existing or "evaluation_measure" not in existing:
            existing = header + ("\n" + existing.lstrip() if existing.strip() else "")
        block = (
            "\n\n@evaluation_measure\n"
            f"def {name}(ctx):\n"
            f'    """Not written yet: fails until it checks something."""\n'
            "    return fail_reason('not_implemented')\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        replace_file(path, existing.rstrip() + "\n" + block)

    def update_measure(
        self, name: str, *, label: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        self._measure(name)

        def update(block: Any) -> None:
            listed = block.get("measures")
            if not isinstance(listed, list):
                block["measures"] = []
                listed = block["measures"]
            for index, entry in enumerate(listed):
                entry_name = (
                    entry if isinstance(entry, str) else (entry or {}).get("name")
                )
                if entry_name == name:
                    if isinstance(entry, str):
                        listed[index] = {"name": name}
                        entry = listed[index]
                    break
            else:
                listed.append({"name": name})
                entry = listed[-1]
            for field, value in (("label", label), ("description", description)):
                if value is None:
                    continue
                if _text(value):
                    entry[field] = _text(value)
                else:
                    entry.pop(field, None)

        self._edit_evaluation(update)
        return self._measure(name)

    def remove_measure(self, name: str) -> None:
        """Remove a measure's description from ``evaluation.yml``. Its
        function, if ``measures.py`` has one, is code and stays."""
        found = False

        def update(block: Any) -> None:
            nonlocal found
            listed = block.get("measures") or []
            for index in reversed(range(len(listed))):
                entry = listed[index]
                entry_name = (
                    entry if isinstance(entry, str) else (entry or {}).get("name")
                )
                if entry_name == name:
                    del listed[index]
                    found = True

        self._edit_evaluation(update)
        if not found:
            raise NotFound(f"{name!r} is not described in evaluation.yml")

    # -- scenario sets -----------------------------------------------------

    @staticmethod
    def _read_scenarios(path: Path) -> list[dict[str, Any]]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return []
        if isinstance(data, dict):
            data = data.get("scenarios")
        return [item for item in data or [] if isinstance(item, dict)]

    def scenario_sets(self) -> list[dict[str, Any]]:
        """The project's scenario files (what a suite runs) and each
        generated scenario set."""
        layout = self.layout
        sets: list[dict[str, Any]] = []
        for path in layout.scenario_files():
            sets.append(
                {
                    "kind": PROJECT,
                    "path": str(path),
                    "label": str(path.relative_to(layout.scenarios_dir.resolve())),
                    "scenarios": self._read_scenarios(path),
                    "updated_at": _iso(path),
                    "manifest": None,
                }
            )
        for path in layout.benchmark_files():
            sets.append(
                {
                    "kind": BENCHMARK,
                    "path": str(path),
                    "label": f"benchmarks/{path.name}",
                    "scenarios": self._read_scenarios(path),
                    "updated_at": _iso(path),
                    "manifest": None,
                }
            )
        for folder in layout.generated_sets():
            path = folder / GENERATED_SCENARIOS
            manifest_path = folder / GENERATED_MANIFEST
            try:
                manifest = (
                    json.loads(manifest_path.read_text(encoding="utf-8"))
                    if manifest_path.is_file()
                    else None
                )
            except ValueError:
                manifest = None
            sets.append(
                {
                    "kind": GENERATED,
                    "path": str(path.resolve()),
                    "label": folder.name,
                    "scenarios": self._read_scenarios(path),
                    "updated_at": _iso(path),
                    "manifest": manifest,
                }
            )
        return sets

    def resolve_set(self, value: str | Path) -> Path:
        """A scenario set named the way a person would: a generated set's
        name, or a set file's path."""
        candidate = Path(value)
        if not candidate.suffix and len(candidate.parts) == 1:
            candidate = self.layout.generated_set_dir(str(value)) / GENERATED_SCENARIOS
        return Path(self.scenario_set(candidate)["path"])

    def scenario_set(self, path: str | Path) -> dict[str, Any]:
        resolved = self._inside(path)
        for scenario_set in self.scenario_sets():
            if Path(scenario_set["path"]) == resolved:
                return scenario_set
        raise NotFound(f"No scenario set at {path}")

    def _edit_scenarios(self, path: Path, update: Callable[[Any], Any]) -> Any:
        """Edit the scenario list in ``path`` in place; ``update`` gets the
        list and returns a result, or ``False`` for no change."""
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        doc_yaml, doc = load_document(text, sequence=True)
        if isinstance(doc, list):
            entries = doc
        else:
            if not isinstance(doc.get("scenarios"), list):
                doc["scenarios"] = []
            entries = doc["scenarios"]
        result = update(entries)
        if result is not False:
            path.parent.mkdir(parents=True, exist_ok=True)
            replace_file(path, dump_document(doc_yaml, doc))
        return result

    @staticmethod
    def _locate(
        entries: Sequence[Any],
        *,
        scenario_id: str | None,
        name: str | None,
        index: int | None,
    ) -> int:
        """Which entry a request means: by id, else name, else position. An
        id or name that no longer matches the position refuses the edit, so
        an edit made against a stale view does not change another scenario."""
        if scenario_id:
            for position, entry in enumerate(entries):
                if isinstance(entry, dict) and str(entry.get("id")) == str(scenario_id):
                    return position
        elif name:
            for position, entry in enumerate(entries):
                if (
                    isinstance(entry, dict)
                    and entry.get("name") == name
                    and not entry.get("id")
                ):
                    return position
        elif index is not None and 0 <= index < len(entries):
            return index
        raise NotFound("The scenario is not in that set (it may have changed)")

    @staticmethod
    def _clean_scenario(scenario: Mapping[str, Any]) -> dict[str, Any]:
        cleaned = {
            key: value
            for key, value in scenario.items()
            if value not in (None, "", [], {}) and not str(key).startswith("_")
        }
        if not _text(cleaned.get("name")):
            raise EvalWorkspaceError("A scenario needs a name")
        if not any(cleaned.get(k) for k in ("prompt", "prompts", "goal")):
            raise EvalWorkspaceError("A scenario needs a prompt, prompts or a goal")
        return cleaned

    def _check_unique(
        self, entries: Sequence[Any], scenario: Mapping[str, Any], skip: int | None
    ) -> None:
        for position, entry in enumerate(entries):
            if position == skip or not isinstance(entry, dict):
                continue
            if scenario.get("id") and str(entry.get("id")) == str(scenario["id"]):
                raise EvalWorkspaceError(
                    f"Scenario id {scenario['id']!r} is used in this set"
                )
            if (
                not scenario.get("id")
                and not entry.get("id")
                and entry.get("name") == scenario["name"]
            ):
                raise EvalWorkspaceError(
                    f"Scenario {scenario['name']!r} is in this set"
                )

    def add_scenario(self, set_path: str | Path, scenario: Mapping[str, Any]) -> dict:
        path = Path(self.scenario_set(set_path)["path"])
        cleaned = self._clean_scenario(scenario)

        def update(entries: Any) -> None:
            self._check_unique(entries, cleaned, None)
            entries.append(cleaned)

        self._edit_scenarios(path, update)
        return self.scenario_set(path)

    def update_scenario(
        self,
        set_path: str | Path,
        scenario: Mapping[str, Any],
        *,
        scenario_id: str | None = None,
        name: str | None = None,
        index: int | None = None,
    ) -> dict:
        """Replace a scenario with ``scenario``; fields it leaves out are removed."""
        path = Path(self.scenario_set(set_path)["path"])
        cleaned = self._clean_scenario(scenario)

        def update(entries: Any) -> None:
            position = self._locate(
                entries, scenario_id=scenario_id, name=name, index=index
            )
            self._check_unique(entries, cleaned, position)
            entries[position] = merge_into(entries[position], cleaned)

        self._edit_scenarios(path, update)
        return self.scenario_set(path)

    def remove_scenario(
        self,
        set_path: str | Path,
        *,
        scenario_id: str | None = None,
        name: str | None = None,
        index: int | None = None,
    ) -> dict:
        path = Path(self.scenario_set(set_path)["path"])

        def update(entries: Any) -> None:
            del entries[
                self._locate(entries, scenario_id=scenario_id, name=name, index=index)
            ]

        self._edit_scenarios(path, update)
        return self.scenario_set(path)

    def promote_scenario(
        self,
        set_path: str | Path,
        *,
        scenario_id: str | None = None,
        name: str | None = None,
        index: int | None = None,
    ) -> dict:
        """Copy a generated scenario into ``scenarios/promoted.yml``, so the
        suite runs it."""
        source = self.scenario_set(set_path)
        if source["kind"] != GENERATED:
            raise EvalWorkspaceError("Only generated scenarios are promoted")
        entries = source["scenarios"]
        scenario = dict(
            entries[
                self._locate(entries, scenario_id=scenario_id, name=name, index=index)
            ]
        )
        target = self.layout.scenarios_dir / "promoted.yml"
        cleaned = self._clean_scenario(scenario)

        def update(target_entries: Any) -> None:
            self._check_unique(target_entries, cleaned, None)
            target_entries.append(cleaned)

        self._edit_scenarios(target, update)
        return self.scenario_set(target)

    def remove_scenario_set(self, set_path: str | Path) -> None:
        """Delete a generated set (its folder) or a project scenario file.
        Its saved runs and the judge datasets built from it stay."""
        scenario_set = self.scenario_set(set_path)
        path = Path(scenario_set["path"])
        if scenario_set["kind"] == GENERATED:
            shutil.rmtree(path.parent)
        else:
            path.unlink()

    @staticmethod
    def _set_name(name: str) -> str:
        name = (name or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise EvalWorkspaceError(
                "A scenario set's name is letters, digits, - and _ (it names its folder)"
            )
        return name

    def _combinations_path(self, name: str) -> Path:
        return self.layout.generated_set_dir(self._set_name(name)) / COMBINATIONS_FILE

    def combinations(self, name: str) -> dict[str, Any]:
        """A set's reviewed combinations of dimension values."""
        path = self._combinations_path(name)
        if not path.is_file():
            raise NotFound(f"No combinations drafted for {name!r}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return {
            "name": name,
            "path": str(path),
            "combinations": [dict(c) for c in data.get("combinations") or []],
            "generated": (path.parent / GENERATED_SCENARIOS).is_file(),
        }

    def drafts(self) -> list[dict[str, Any]]:
        """Sets whose combinations are drafted but not yet generated."""
        root = self.layout.generated_dir
        if not root.is_dir():
            return []
        return [
            self.combinations(folder.name)
            for folder in sorted(root.iterdir())
            if (folder / COMBINATIONS_FILE).is_file()
            and not (folder / GENERATED_SCENARIOS).is_file()
        ]

    def _check_combinations(self, combinations: Sequence[Mapping[str, Any]]) -> list:
        allowed = {
            d["name"]: {v["name"] for v in d["values"]} for d in self.dimensions()
        }
        checked = []
        for combination in combinations:
            entry = {str(k): str(v) for k, v in combination.items()}
            if set(entry) != set(allowed):
                raise EvalWorkspaceError(
                    "A combination gives one value of each dimension: "
                    + ", ".join(sorted(allowed))
                )
            for dimension, value in entry.items():
                if value not in allowed[dimension]:
                    raise EvalWorkspaceError(f"{dimension} has no value {value!r}")
            if entry not in checked:
                checked.append(entry)
        return checked

    def set_combinations(
        self, name: str, combinations: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Replace a draft's combinations with the reviewed ones."""
        path = self._combinations_path(name)
        if (path.parent / GENERATED_SCENARIOS).is_file():
            raise EvalWorkspaceError(f"{name!r} is already generated")
        checked = self._check_combinations(combinations)
        path.parent.mkdir(parents=True, exist_ok=True)
        replace_file(
            path,
            yaml.safe_dump(
                {
                    "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "combinations": checked,
                },
                sort_keys=False,
            ),
        )
        return self.combinations(name)

    def remove_draft(self, name: str) -> None:
        """Delete a set drafted but not generated."""
        path = self._combinations_path(name)
        if (path.parent / GENERATED_SCENARIOS).is_file() or not path.is_file():
            raise NotFound(f"No draft {name!r}")
        shutil.rmtree(path.parent)

    def draft_combinations(
        self,
        name: str,
        *,
        count: int = 20,
        seed: int | None = None,
        dimension_values: Mapping[str, Sequence[str]] | None = None,
    ) -> dict[str, Any]:
        """Draft ``count`` combinations of dimension values for a new
        generated set, as ``generated/<name>/combinations.yml``, for a person
        to review (remove the ones real users never present, add missing
        ones) before generating. ``dimension_values`` limits which values of
        each dimension are drawn."""
        from omniagents.core.eval.scenarios import draft_combinations

        path = self._combinations_path(name)
        if (path.parent / GENERATED_SCENARIOS).is_file():
            raise EvalWorkspaceError(f"A generated set named {name!r} exists")
        dimensions = self.dimensions()
        if not dimensions:
            raise EvalWorkspaceError("Describe the dimensions before drafting")
        filters = {
            str(dim): _names(list(values))
            for dim, values in (dimension_values or {}).items()
            if _names(list(values))
        }
        drafted = draft_combinations(
            dimensions, count=count, seed=seed, filters=filters or None
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        replace_file(
            path,
            yaml.safe_dump(
                {
                    "drafted_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "combinations": drafted,
                },
                sort_keys=False,
            ),
        )
        return self.combinations(name)

    async def generate_scenarios(
        self,
        name: str,
        *,
        count: int | None = None,
        seed: int | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Generate the set ``generated/<name>/`` from its reviewed
        combinations (:meth:`draft_combinations`), about ``count`` scenarios
        in all. Unless ``synthetic_data_generation.realism`` is ``false``,
        queries a model rates below its ``min_score`` (default 3 of 5) are
        left out, in ``filtered.yml`` beside the set."""
        from omniagents.core.eval.review_assist import model_asker
        from omniagents.core.eval.scenarios import generate_scenarios

        combinations = self.combinations(name)["combinations"]
        combinations = self._check_combinations(combinations)
        config = self.config()
        generation = config.config.get("synthetic_data_generation") or {}
        realism = generation.get("realism", {})
        ask = None
        min_score = 3
        if realism is not False:
            realism = realism if isinstance(realism, dict) else {}
            min_score = int(realism.get("min_score") or 3)
            model = realism.get("model") or (
                (generation.get("generator") or {}).get("agent") or {}
            ).get("model")
            from omniagents.core.eval.review_assist import DEFAULT_MODEL

            ask = model_asker(str(model or DEFAULT_MODEL))
        workflow_name = self._start_tracing(config, f"generate_{name}")
        _scenarios, out_path, _ = await generate_scenarios(
            config.agent_path,
            combinations=combinations,
            count=count,
            seed=seed,
            set_name=name,
            output=self.layout.generated_set_dir(name) / GENERATED_SCENARIOS,
            workflow_name=workflow_name,
            progress_cb=progress_cb,
            eval_config=config,
            realism=ask,
            min_realism=min_score,
        )
        return self.scenario_set(out_path)

    # -- judges ------------------------------------------------------------

    def judges(self) -> list[dict[str, Any]]:
        """Each judge: where it is defined, its model, what it reads, the
        failure category it decides, its instructions and template, and
        whether it is validated as it is now (``status``, ``validation``)."""
        config = self.config()
        out = []
        for key, cfg in sorted((config.config.get("judges") or {}).items()):
            if not isinstance(cfg, dict):
                continue
            agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
            reads = cfg.get("input") if isinstance(cfg.get("input"), dict) else {}
            instructions_path = agent.get("instructions_file")
            template_path = reads.get("template_file")
            out.append(
                {
                    "key": str(key),
                    "label": _text(agent.get("label")) or str(key),
                    "model": _text(agent.get("model")),
                    "source": str(reads.get("source") or "final_assistant_text"),
                    "failure_category": _text(
                        cfg.get("failure_category") or cfg.get("category")
                    ),
                    "instructions": (
                        Path(instructions_path).read_text(encoding="utf-8")
                        if instructions_path
                        else str(agent.get("instructions") or "")
                    ),
                    "template": (
                        Path(template_path).read_text(encoding="utf-8")
                        if template_path
                        else str(reads.get("template") or "")
                    ),
                    "instructions_path": instructions_path,
                    "template_path": template_path,
                    "defined_in": str(config.judge_sources.get(str(key)) or ""),
                    # few-shot examples: answer ids from its dataset's train split
                    "examples": [str(i) for i in cfg.get("examples") or []],
                    # validated / changed (since validation) / unvalidated
                    **validation_status(self.layout, str(key), cfg),
                }
            )
        return out

    def judge(self, key: str) -> dict[str, Any]:
        for judge in self.judges():
            if judge["key"] == key:
                return judge
        raise NotFound(f"No judge {key!r}")

    def add_judge(
        self,
        key: str,
        *,
        model: str,
        source: str = "final_assistant_text",
        failure_category: str | None = None,
        label: str | None = None,
        instructions: str | None = None,
        template: str | None = None,
    ) -> dict[str, Any]:
        """A judge in ``judges/<key>/`` from the project template, with a
        ``<key>_agent`` measure that reports its verdict; deciding a failure
        category lists that measure under the category."""
        from omniagents.core.project.scaffold import scaffold_judge

        key = slugify(key)
        if not key or not key[0].isalpha():
            raise EvalWorkspaceError("A judge's key must start with a letter")
        if any(judge["key"] == key for judge in self.judges()):
            raise EvalWorkspaceError(f"A judge named {key!r} exists")
        scaffold_judge(
            str(self.manifest),
            key,
            model=model,
            source=source,
            add_measure=True,
            failure_category=failure_category or None,
        )
        return self.update_judge(
            key,
            label=label,
            failure_category=failure_category,
            instructions=instructions,
            template=template,
        )

    def _judge_file(self, key: str) -> Path:
        """``judges/<key>/judge.yml``."""
        return Path(self.judge(key)["defined_in"])

    def update_judge(
        self,
        key: str,
        *,
        model: str | None = None,
        source: str | None = None,
        failure_category: str | None = None,
        label: str | None = None,
        instructions: str | None = None,
        template: str | None = None,
    ) -> dict[str, Any]:
        """Change a judge (``None`` leaves a field as it is; an empty
        ``failure_category`` clears it). Instructions and template are saved
        in the judge's files, or inline when it has none."""
        current = self.judge(key)
        path = self._judge_file(key)
        doc_yaml, entry = load_document(path.read_text(encoding="utf-8"))
        agent = entry.get("agent")
        if not isinstance(agent, dict):
            entry["agent"] = {}
            agent = entry["agent"]
        reads = entry.get("input")
        if not isinstance(reads, dict):
            entry["input"] = {}
            reads = entry["input"]
        if model is not None:
            agent["model"] = model
        if label is not None:
            agent["label"] = label
        if source is not None:
            reads["source"] = source
        if failure_category is not None:
            if failure_category.strip():
                entry["failure_category"] = failure_category.strip()
            else:
                entry.pop("failure_category", None)
        if instructions is not None and not current["instructions_path"]:
            agent["instructions"] = instructions
        if template is not None and not current["template_path"]:
            reads["template"] = template
        replace_file(path, dump_document(doc_yaml, entry))
        if instructions is not None and current["instructions_path"]:
            replace_file(Path(current["instructions_path"]), instructions)
        if template is not None and current["template_path"]:
            replace_file(Path(current["template_path"]), template)
        if failure_category and failure_category.strip():
            from omniagents.core.project.scaffold import add_category_measure

            add_category_measure(
                self.evaluation_file, failure_category.strip(), f"{key}_agent"
            )
        return self.judge(key)

    def set_judge_examples(self, key: str, example_ids: Sequence[str]) -> dict:
        """Make answers from the train split of the judge's dataset its
        few-shot examples (``examples`` in its ``judge.yml``), each shown with
        the reviewer's notes as its critique. Refused, leaving the file as it
        was, when one is not in the train split or has no notes."""
        path = self._judge_file(key)
        before = path.read_text(encoding="utf-8")
        doc_yaml, entry = load_document(before)
        ids = [str(i) for i in example_ids]
        if ids:
            entry["examples"] = ids
        else:
            entry.pop("examples", None)
        replace_file(path, dump_document(doc_yaml, entry))
        try:
            return self.judge(key)
        except ValueError as exc:
            replace_file(path, before)
            raise EvalWorkspaceError(str(exc)) from exc

    def remove_judge(self, key: str) -> None:
        """Delete a judge's folder. Its measure function stays in
        ``measures.py``."""
        shutil.rmtree(self._judge_file(key).parent)

    # -- reviews -----------------------------------------------------------

    def reviews(self) -> list[dict[str, Any]]:
        """Every review of an evaluation answer (:mod:`reviews`)."""
        from omniagents.core.eval.reviews import list_reviews

        return list_reviews(self.layout)

    def review(self, group_id: str) -> dict[str, Any] | None:
        from omniagents.core.eval.reviews import review_of

        return review_of(self.layout, group_id)

    def is_reviewable(self, group_id: str) -> bool:
        """Whether ``group_id`` is a conversation of a saved run (or already
        reviewed), so its review belongs in ``reviews/``."""
        from omniagents.core.eval.results import find_conversation

        return (
            self.review(group_id) is not None
            or find_conversation(self.layout, group_id) is not None
        )

    def save_review(
        self, group_id: str, *, reviewer: str | None, **changes: Any
    ) -> dict[str, Any] | None:
        """Record a person's review of an evaluation answer: ``verdict``,
        ``failure_categories`` (ids of the project's categories), ``notes``
        and ``marked``; fields not given keep what the review had. The whole
        conversation is kept with it when a judge of the project reads
        conversations, since every review is an example for every
        category's judge. Returns the review, or ``None`` once it is empty."""
        from omniagents.core.eval.reviews import ReviewError, save_review

        if changes.get("failure_categories") is not None:
            known = {category["id"] for category in self.failure_categories()}
            unknown = [
                c for c in changes["failure_categories"] if slugify(c) not in known
            ]
            if unknown:
                raise EvalWorkspaceError(
                    "Unknown failure category: " + ", ".join(unknown)
                )
            changes["failure_categories"] = [
                slugify(c) for c in changes["failure_categories"]
            ]
        keep_history = any(judge["source"] == "history" for judge in self.judges())
        try:
            return save_review(
                self.layout,
                group_id,
                reviewer=reviewer,
                keep_history=keep_history,
                **changes,
            )
        except ReviewError as exc:
            raise EvalWorkspaceError(str(exc)) from exc

    def remove_review(self, group_id: str) -> None:
        from omniagents.core.eval.reviews import ReviewError, remove_review

        try:
            remove_review(self.layout, group_id)
        except ReviewError as exc:
            raise NotFound(str(exc)) from exc

    # -- review assistant --------------------------------------------------

    def review_assist(self) -> dict[str, Any]:
        """The review assistant's sample, pending proposals and suggestions
        (:mod:`review_assist`)."""
        from omniagents.core.eval.review_assist import state

        return state(self.layout)

    def draw_review_sample(self, *, size: int = 20, seed: int = 0) -> dict[str, Any]:
        """Which unreviewed evaluation answers to read next."""
        from omniagents.core.eval.review_assist import draw_sample

        return draw_sample(self.layout, size=size, seed=seed)

    def _assistant(self) -> Any:
        from omniagents.core.eval.review_assist import assistant_model, model_asker

        return model_asker(assistant_model(self._evaluation_block()))

    async def propose_failure_modes(
        self,
        *,
        other_notes: Sequence[Mapping[str, Any]] = (),
        ask: Any = None,
    ) -> list[dict[str, Any]]:
        """Group people's review notes into failure modes: the notes on
        evaluation answers, and ``other_notes`` (``{id, verdict, notes}``,
        e.g. reviews of other conversations kept elsewhere)."""
        from omniagents.core.eval.review_assist import propose_failure_modes

        notes = [
            {"id": r["group_id"], "verdict": r.get("verdict"), "notes": r.get("notes")}
            for r in self.reviews()
        ] + [dict(n) for n in other_notes]
        try:
            return await propose_failure_modes(
                self.layout, notes, self.failure_categories(), ask or self._assistant()
            )
        except ValueError as exc:
            raise EvalWorkspaceError(str(exc)) from exc

    def _add_label(self, group_id: str, category: str, reviewer: str | None) -> None:
        """Label an evaluation answer with ``category``; an answer showing a
        failure is unacceptable unless a person already judged it."""
        review = self.review(group_id) or {}
        labels = sorted({*(review.get("failure_categories") or []), category})
        self.save_review(
            group_id,
            reviewer=reviewer,
            failure_categories=labels,
            verdict=review.get("verdict") or "unacceptable",
        )

    def accept_proposal(
        self,
        proposal_id: str,
        *,
        reviewer: str | None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Accept a proposed failure mode: the category (an existing one, or
        a new one named ``name``) and the label on each review it grouped.
        Returns the category and the ids that are not evaluation answers'
        reviews (``unapplied``), for the caller to label where it keeps them."""
        from omniagents.core.eval.review_assist import take_proposal

        try:
            proposal = take_proposal(self.layout, proposal_id)
        except KeyError as exc:
            raise NotFound(str(exc)) from exc
        if proposal["existing_category"]:
            category = self._category(proposal["existing_category"])
        else:
            chosen = (name or proposal["name"]).strip()
            existing = next(
                (c for c in self.failure_categories() if c["id"] == slugify(chosen)),
                None,
            )
            category = existing or self.add_failure_category(
                chosen,
                description=(
                    description if description is not None else proposal["description"]
                ),
            )
        unapplied = []
        for group_id in proposal["review_ids"]:
            if self.review(group_id) is not None:
                self._add_label(group_id, category["id"], reviewer)
            else:
                unapplied.append(group_id)
        return {"category": self._category(category["id"]), "unapplied": unapplied}

    def dismiss_proposal(self, proposal_id: str) -> None:
        from omniagents.core.eval.review_assist import take_proposal

        try:
            take_proposal(self.layout, proposal_id)
        except KeyError as exc:
            raise NotFound(str(exc)) from exc

    async def suggest_labels(self, category_id: str, *, ask: Any = None) -> list[dict]:
        """Read every evaluation answer for failure mode ``category_id`` and
        add a pending suggestion for each that may show it."""
        from omniagents.core.eval.review_assist import suggest_labels

        category = self._category(slugify(category_id))
        return await suggest_labels(self.layout, category, ask or self._assistant())

    def decide_suggestions(
        self, ids: Sequence[str], *, accept: bool, reviewer: str | None
    ) -> list[dict[str, Any]]:
        """Accept (label the answers) or dismiss pending suggestions."""
        from omniagents.core.eval.review_assist import decide_suggestions

        decided = decide_suggestions(
            self.layout, ids, "accepted" if accept else "dismissed"
        )
        if accept:
            for suggestion in decided:
                self._add_label(
                    suggestion["group_id"], suggestion["category"], reviewer
                )
        return decided

    # -- judge datasets ----------------------------------------------------

    def judge_datasets(self) -> list[dict[str, Any]]:
        """For each failure category with one, its judge dataset: reviewed
        answers labelled positive (reviewers saw the category) or negative,
        split into train, dev and test."""
        root = self.layout.judge_datasets_dir
        if not root.is_dir():
            return []
        datasets = []
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            manifest_path = folder / DATASET_MANIFEST
            if not manifest_path.is_file():
                continue
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            splits = []
            for split in SPLIT_NAMES:
                path = folder / f"{split}.yml"
                counts = (manifest.get("counts") or {}).get(split) or {}
                splits.append(
                    {
                        "split": split,
                        "path": str(path),
                        "scenario_count": int(counts.get("fails") or 0)
                        + int(counts.get("passes") or 0),
                        # A failing answer shows the category: its judge
                        # should say Fail.
                        "fails": int(counts.get("fails") or 0),
                        "passes": int(counts.get("passes") or 0),
                        "updated_at": _iso(path),
                    }
                )
            datasets.append(
                {"category": folder.name, "manifest": manifest, "splits": splits}
            )
        return datasets

    def judge_dataset(self, category: str) -> dict[str, Any]:
        for dataset in self.judge_datasets():
            if dataset["category"] == category:
                return dataset
        raise NotFound(f"No judge dataset for {category!r}")

    def create_judge_dataset(
        self,
        category: str,
        *,
        fractions: tuple[float, float, float] = (0.15, 0.425, 0.425),
        seed: int = 42,
    ) -> dict[str, Any]:
        """Build (or rebuild) the judge dataset for ``category`` from every
        reviewed answer. An answer's split depends only on it and the seed,
        so rebuilding after more reviews keeps every earlier example where
        it was."""
        from omniagents.core.eval.reviews import ReviewError, build_judge_dataset

        self._category(category)
        try:
            build_judge_dataset(self.layout, category, fractions=fractions, seed=seed)
        except ReviewError as exc:
            raise EvalWorkspaceError(str(exc)) from exc
        return self.judge_dataset(category)

    def judge_dataset_split(self, split_path: str | Path) -> dict[str, Any]:
        """One split's examples: each reviewed answer, its label, and the
        reply the judge reads."""
        path = self._inside(split_path)
        for dataset in self.judge_datasets():
            for split in dataset["splits"]:
                if Path(split["path"]) == path:
                    return {
                        **dataset,
                        "split": split,
                        "scenarios": self._read_scenarios(path),
                    }
        raise NotFound(f"No judge dataset split at {split_path}")

    def remove_judge_dataset(self, category: str) -> None:
        self.judge_dataset(category)
        shutil.rmtree(self.layout.judge_dataset_dir(category))

    # -- holdout -----------------------------------------------------------

    def holdout(self) -> dict[str, Any] | None:
        """The project's train/dev/test holdout of its scenarios, if made."""
        manifest = self.layout.holdout_dir / "manifest.yml"
        if not manifest.is_file():
            return None
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        return {**data, "updated_at": _iso(manifest)}

    def create_holdout(
        self,
        *,
        strata: Sequence[str] = (),
        fractions: tuple[float, float, float] = (0.5, 0.25, 0.25),
        seed: int = 42,
        select: str | None = None,
        exclude: str | None = None,
    ) -> dict[str, Any]:
        """Assign each of the project's scenarios to train, dev or test,
        stratified by ``strata`` fields (``expect.tone``, ``tags``), so
        ``run(split="test")`` scores the agent on scenarios it was not tuned on."""
        from omniagents.core.eval.holdout import (
            create_holdout,
            holdout_manifest,
            write_holdout,
        )

        scenarios = list(
            self.config(select=select, exclude=exclude).config.get("scenarios") or []
        )
        if not scenarios:
            raise EvalWorkspaceError("The project has no scenarios to hold out")
        try:
            assignment = create_holdout(
                scenarios, strata=list(strata), fractions=fractions, seed=seed
            )
        except ValueError as exc:
            raise EvalWorkspaceError(str(exc)) from exc
        manifest = holdout_manifest(
            assignment, scenarios, list(strata), fractions, seed
        )
        write_holdout(self.layout, assignment, manifest)
        holdout = self.holdout()
        assert holdout is not None
        return holdout

    # -- runs --------------------------------------------------------------

    @staticmethod
    def _start_tracing(config: EvalConfig, label: str) -> str | None:
        """Record the runs' traces locally when the project traces to Studio."""
        tracing = config.tracing_config or {}
        if str(tracing.get("provider") or "").lower() != "studio":
            return None
        from omniagents.core.tracing import local

        stamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        name = f"{tracing.get('name') or config.agent_path.parent.name}_{label}_{stamp}"
        local.init(
            project=str(tracing.get("project") or "evaluation"),
            name=name,
            replace_default=True,
            **(tracing.get("settings") or {}),
        )
        return name

    def run(
        self,
        *,
        scenario_set: str | Path | None = None,
        select: str | None = None,
        exclude: str | None = None,
        split: str | None = None,
        include_unmeasured: bool | None = None,
        max_turns: int | None = None,
        name: str | None = None,
        source: str = "cli",
        progress_cb: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run scenarios against the agent and save the run with its manifest.

        Without ``scenario_set`` this is the project's suite (its scenarios,
        optionally one holdout ``split``; unmeasured ones are skipped unless
        ``include_unmeasured``). With one, every scenario in that set runs.
        Either way it is saved in ``runs/`` with a copy of the agent, and
        keeps each conversation, which judges and reviews read. Returns the
        saved run (:func:`results.read_run`).
        """
        from omniagents.core.eval.pipeline import run_evaluation

        config = self.config(select=select, exclude=exclude, split=split)
        eval_cfg = dict(config.config)
        if scenario_set is not None:
            if split:
                raise EvalWorkspaceError(
                    "A holdout split selects the project's scenarios"
                )
            from omniagents.core.eval.loader import filter_scenarios

            chosen = self.scenario_set(scenario_set)
            set_path = Path(chosen["path"])
            label = name or chosen["label"]
            scenarios = filter_scenarios(
                self._read_scenarios(set_path), select, exclude
            )
            if not scenarios:
                raise EvalWorkspaceError("No scenarios in that set match")
            for scenario in scenarios:
                scenario["_source_path"] = str(set_path)
            eval_cfg["scenarios"] = scenarios
            measured_only = (
                False if include_unmeasured is None else not include_unmeasured
            )
        else:
            if not eval_cfg.get("scenarios"):
                raise EvalWorkspaceError("The project has no scenarios to run")
            set_path = None
            label = name or "suite"
            measured_only = not include_unmeasured
        if max_turns is not None:
            eval_cfg["defaults"] = {
                **(eval_cfg.get("defaults") or {}),
                "max_turns": max_turns,
            }
        data = run_evaluation(
            config.agent_path,
            tracing_cfg=config.tracing_config,
            workflow_base_name=name or None,
            include_unmeasured=not measured_only,
            progress_cb=progress_cb,
            eval_config=config,
            eval_cfg=eval_cfg,
            include_history=True,
        )
        saved = save_evaluation_run(
            self.layout,
            data,
            source=source,
            label=label,
            scenario_set=set_path,
            agent=self.agent_key(),
            agent_path=config.agent_path,
            workflow_name=data.get("workflow_name"),
        )
        return read_run(saved)

    def runs(self) -> list[dict[str, Any]]:
        """Every saved run of the project's and generated scenarios, newest first."""
        return list_runs(self.layout)

    def _run_path(self, path: str | Path) -> Path:
        resolved = self._inside(path)
        if not any(str(resolved) == entry.get("path") for entry in self.runs()):
            raise NotFound(f"No saved run at {path}")
        return resolved

    def read_run(self, path: str | Path) -> dict[str, Any]:
        return read_run(self._run_path(path))

    def compare_runs(self, a: str | Path, b: str | Path) -> dict[str, Any]:
        """How saved run ``b`` differs from ``a`` (:func:`results.compare_runs`)."""
        return compare_runs(self.read_run(a), self.read_run(b))

    def keep_run(self, path: str | Path, name: str) -> dict[str, Any]:
        """Keep a saved run as the committed baseline ``name``: the evidence,
        with the agent it tested, for a number someone reports."""
        from omniagents.core.eval.results import keep_run

        resolved = self._run_path(path)
        if resolved.parent.parent != self.layout.runs_dir:
            raise EvalWorkspaceError("Only a saved run is kept as a baseline")
        try:
            return keep_run(self.layout, resolved.parent.name, name)
        except (ValueError, FileNotFoundError) as exc:
            raise EvalWorkspaceError(str(exc)) from exc

    def remove_baseline(self, name: str) -> None:
        from omniagents.core.eval.results import remove_baseline

        try:
            remove_baseline(self.layout, name)
        except FileNotFoundError as exc:
            raise NotFound(str(exc)) from exc
