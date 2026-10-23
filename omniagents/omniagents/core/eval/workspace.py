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

Where things live, relative to the evaluations directory (``evaluations/``
unless ``project.yml`` says otherwise):

- ``evaluation.yml``: dimensions (``synthetic_data_generation.dimensions``),
  failure categories, measure descriptions, defaults, inline judges;
- ``scenarios.yml`` and ``scenarios/**.yml``: the project's scenarios;
- ``judges/<key>/judge.yml``: judges, beside their instructions and template;
- ``measures.py``: the project's measure functions;
- ``splits/eval/``: the holdout;
- ``results/``: runs of the project's scenarios.

Generated scenario sets live under ``artifacts/eval/<set>/`` in the project:
``generated_scenarios.yml``, a ``manifest.json``, their runs in ``results/``
and judge datasets in ``splits/<category>/{train,dev,test}.yml``.
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

from omniagents.core.eval.loader import EvalConfig, evaluations_dir, load_eval_config
from omniagents.core.eval.results import (
    compare_runs,
    latest_conversations,
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


class EvalWorkspaceError(ValueError):
    """A request the workspace refused; the message says why."""


class NotFound(EvalWorkspaceError):
    """What the request names does not exist."""


def slugify(value: str) -> str:
    """A failure category's id: its name, lowercased, words joined by ``_``."""
    token = re.sub(r"[^a-z0-9\-_]+", "_", (value or "").strip().lower())
    return re.sub(r"_+", "_", token).strip("_")


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
    def evaluation_file(self) -> Path:
        return self.eval_dir / "evaluation.yml"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts" / "eval"

    def config(
        self,
        *,
        select: str | None = None,
        exclude: str | None = None,
        split: str | None = None,
    ) -> EvalConfig:
        """The evaluation as a run loads it."""
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
                self.eval_dir / "evaluation.yml",
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
        path = self.eval_dir / "measures.py"
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

    def _project_scenario_files(self) -> list[Path]:
        """The files whose scenarios a suite runs, as the loader finds them."""
        files: list[Path] = []
        for candidate in (
            self.root / "evaluations" / "scenarios.yml",
            self.eval_dir / "scenarios.yml",
        ):
            if candidate.is_file() and candidate.resolve() not in files:
                files.append(candidate.resolve())
        folder = self.eval_dir / "scenarios"
        if folder.is_dir():
            files += [p.resolve() for p in sorted(folder.rglob("*.yml"))]
        return files

    def _generated_dirs(self) -> list[Path]:
        if not self.artifacts_dir.is_dir():
            return []
        return [
            entry
            for entry in sorted(self.artifacts_dir.iterdir())
            if (entry / "generated_scenarios.yml").is_file()
        ]

    @staticmethod
    def _read_scenarios(path: Path) -> list[dict[str, Any]]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return []
        if isinstance(data, dict):
            if isinstance(data.get("evaluation"), dict):
                data = data["evaluation"]
            data = data.get("scenarios")
        return [item for item in data or [] if isinstance(item, dict)]

    def scenario_sets(self) -> list[dict[str, Any]]:
        """The project's scenario files (what a suite runs; scenarios written
        inline in ``evaluation.yml`` when there are none) and each generated
        scenario set."""
        sets: list[dict[str, Any]] = []
        files = self._project_scenario_files()
        if (
            not files
            and self.evaluation_file.is_file()
            and self._read_scenarios(self.evaluation_file)
        ):
            files = [self.evaluation_file]
        for path in files:
            sets.append(
                {
                    "kind": PROJECT,
                    "path": str(path),
                    "label": (
                        str(path.relative_to(self.eval_dir))
                        if self.eval_dir in path.parents
                        else path.name
                    ),
                    "scenarios": self._read_scenarios(path),
                    "updated_at": _iso(path),
                    "manifest": None,
                }
            )
        for folder in self._generated_dirs():
            path = folder / "generated_scenarios.yml"
            manifest_path = folder / "manifest.json"
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
                    "label": (manifest or {}).get("name") or folder.name,
                    "scenarios": self._read_scenarios(path),
                    "updated_at": _iso(path),
                    "manifest": manifest,
                }
            )
        return sets

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
            holder = doc
            if isinstance(doc.get("evaluation"), dict) and "scenarios" not in doc:
                holder = doc["evaluation"]
            if not isinstance(holder.get("scenarios"), list):
                holder["scenarios"] = []
            entries = holder["scenarios"]
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

    def label_scenario(
        self,
        set_path: str | Path,
        *,
        failure_categories: Sequence[str],
        review: Mapping[str, Any] | None = None,
        scenario_id: str | None = None,
        name: str | None = None,
    ) -> None:
        """Record a reviewer's verdict on a scenario: the failure categories
        its run showed, and the review (``judgment``, ``notes``) under
        ``analysis``. A judge dataset is built from these labels."""
        path = Path(self.scenario_set(set_path)["path"])
        slugs = sorted({slugify(c) for c in failure_categories if slugify(c)})

        def update(entries: Any) -> None:
            entry = entries[
                self._locate(entries, scenario_id=scenario_id, name=name, index=None)
            ]
            if slugs:
                entry["failure_categories"] = slugs
            else:
                entry.pop("failure_categories", None)
            if review:
                entry["analysis"] = {k: v for k, v in review.items() if v is not None}
            else:
                entry.pop("analysis", None)

        self._edit_scenarios(path, update)

    def promote_scenario(
        self,
        set_path: str | Path,
        *,
        scenario_id: str | None = None,
        name: str | None = None,
        index: int | None = None,
    ) -> dict:
        """Copy a generated scenario into the project's ``scenarios.yml``,
        so the suite runs it."""
        source = self.scenario_set(set_path)
        if source["kind"] != GENERATED:
            raise EvalWorkspaceError("Only generated scenarios are promoted")
        entries = source["scenarios"]
        scenario = dict(
            entries[
                self._locate(entries, scenario_id=scenario_id, name=name, index=index)
            ]
        )
        scenario.pop("analysis", None)
        target = self.eval_dir / "scenarios.yml"
        cleaned = self._clean_scenario(scenario)

        def update(target_entries: Any) -> None:
            self._check_unique(target_entries, cleaned, None)
            target_entries.append(cleaned)

        self._edit_scenarios(target, update)
        return self.scenario_set(target)

    def remove_scenario_set(self, set_path: str | Path) -> None:
        """Delete a generated set (its whole folder: runs and judge datasets
        too) or a project scenario file."""
        scenario_set = self.scenario_set(set_path)
        path = Path(scenario_set["path"])
        if scenario_set["kind"] == GENERATED:
            shutil.rmtree(path.parent)
        elif path == self.evaluation_file:
            raise EvalWorkspaceError(
                "Scenarios inside evaluation.yml are removed one by one"
            )
        else:
            path.unlink()

    async def generate_scenarios(
        self,
        name: str,
        *,
        count: int | None = None,
        seed: int | None = None,
        dimension_values: Mapping[str, Sequence[str]] | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Generate a set of synthetic scenarios from the dimensions, as
        ``artifacts/eval/<name>/``. ``dimension_values`` limits which values
        of each dimension are drawn."""
        from omniagents.core.eval.scenarios import generate_scenarios

        name = (name or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise EvalWorkspaceError(
                "A scenario set's name is letters, digits, - and _ (it names its folder)"
            )
        config = self.config()
        workflow_name = self._start_tracing(config, f"generate_{name}")
        filters = {
            str(dim): _names(list(values))
            for dim, values in (dimension_values or {}).items()
            if _names(list(values))
        }
        _scenarios, out_path, _ = await generate_scenarios(
            config.agent_path,
            count=count,
            seed=seed,
            set_name=name,
            output=self.artifacts_dir / name / "generated_scenarios.yml",
            workflow_name=workflow_name,
            progress_cb=progress_cb,
            dimension_filters=filters or None,
            eval_config=config,
        )
        return self.scenario_set(out_path)

    # -- judges ------------------------------------------------------------

    def judges(self) -> list[dict[str, Any]]:
        """Each judge: where it is defined, its model, what it reads, the
        failure category it decides, and its instructions and template."""
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

    def _judge_location(self, key: str) -> tuple[Path, Callable[[Any], Any]]:
        """The file defining judge ``key`` and how to find its entry in it."""
        source = Path(self.judge(key)["defined_in"])

        def entry(doc: Any) -> Any:
            for holder in (
                doc,
                doc.get("evaluation") if isinstance(doc, dict) else None,
            ):
                if not isinstance(holder, dict):
                    continue
                judges = holder.get("judges")
                if isinstance(judges, dict) and key in judges:
                    return judges[key]
                if isinstance(judges, list):
                    for item in judges:
                        if (
                            isinstance(item, dict)
                            and (item.get("name") or item.get("key")) == key
                        ):
                            return item
            raise NotFound(f"Judge {key!r} is not in {source}")

        return source, entry

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
        path, entry_of = self._judge_location(key)
        doc_yaml, doc = load_document(path.read_text(encoding="utf-8"))
        entry = entry_of(doc)
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
        replace_file(path, dump_document(doc_yaml, doc))
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

    def remove_judge(self, key: str) -> None:
        """Delete a judge: its folder when it has one to itself, else its
        entry where it is defined. Its measure function stays in
        ``measures.py``."""
        path, entry_of = self._judge_location(key)
        doc_yaml, doc = load_document(path.read_text(encoding="utf-8"))
        holder = doc if "judges" in doc else doc.get("evaluation")
        judges = holder["judges"]
        if (
            path.name == "judge.yml"
            and isinstance(judges, dict)
            and list(judges) == [key]
        ):
            shutil.rmtree(path.parent)
            return
        if isinstance(judges, dict):
            del judges[key]
        else:
            judges.remove(entry_of(doc))
        replace_file(path, dump_document(doc_yaml, doc))

    # -- judge datasets ----------------------------------------------------

    def judge_datasets(self) -> list[dict[str, Any]]:
        """For each generated set and failure category, the train/dev/test
        scenarios (labelled positive or negative for the category) a judge
        of that category is optimized and checked on."""
        datasets = []
        for folder in self._generated_dirs():
            splits_root = folder / "splits"
            if not splits_root.is_dir():
                continue
            for category_dir in sorted(p for p in splits_root.iterdir() if p.is_dir()):
                splits = []
                for split in ("train", "dev", "test"):
                    path = category_dir / f"{split}.yml"
                    if not path.is_file():
                        continue
                    entries = self._read_scenarios(path)
                    splits.append(
                        {
                            "split": split,
                            "path": str(path),
                            "scenario_count": len(entries),
                            "positives": sum(
                                1 for e in entries if e.get("label") is True
                            ),
                            "negatives": sum(
                                1 for e in entries if e.get("label") is False
                            ),
                            "updated_at": _iso(path),
                        }
                    )
                if splits:
                    datasets.append(
                        {
                            "set": folder.name,
                            "set_path": str(folder / "generated_scenarios.yml"),
                            "category": category_dir.name,
                            "splits": splits,
                        }
                    )
        return datasets

    def create_judge_dataset(
        self,
        set_path: str | Path,
        category: str,
        *,
        fractions: tuple[float, float, float] = (0.15, 0.425, 0.425),
        seed: int = 42,
        negatives_ratio: float | None = None,
    ) -> dict[str, Any]:
        """Split a reviewed generated set into a judge dataset for
        ``category``: scenarios labelled with it are positives, the rest
        negatives."""
        from omniagents.core.eval.split import create_splits

        scenario_set = self.scenario_set(set_path)
        if scenario_set["kind"] != GENERATED:
            raise EvalWorkspaceError(
                "Judge datasets are built from generated scenario sets"
            )
        if sum(fractions) <= 0 or any(f < 0 for f in fractions):
            raise EvalWorkspaceError("Train, dev and test fractions must be positive")
        path = Path(scenario_set["path"])
        create_splits(
            path,
            category,
            output_dir=None,
            f_train=fractions[0],
            f_dev=fractions[1],
            f_test=fractions[2],
            seed=seed,
            negatives_ratio=negatives_ratio,
        )
        for dataset in self.judge_datasets():
            if dataset["set"] == path.parent.name and dataset["category"] == category:
                return dataset
        raise EvalWorkspaceError("The judge dataset was not written")

    def judge_dataset_split(self, split_path: str | Path) -> dict[str, Any]:
        """One split's scenarios with the reply each got in the set's newest
        run that included it (what the judge reads)."""
        path = self._inside(split_path)
        for dataset in self.judge_datasets():
            for split in dataset["splits"]:
                if Path(split["path"]) == path:
                    conversations = latest_conversations(
                        Path(dataset["set_path"]).parent
                    )
                    scenarios = []
                    for scenario in self._read_scenarios(path):
                        key = str(scenario.get("id") or scenario.get("name") or "")
                        run = conversations.get(key)
                        scenarios.append(
                            {
                                **scenario,
                                "reply": (
                                    None
                                    if run is None
                                    else run.get("final_assistant_text")
                                ),
                            }
                        )
                    return {**dataset, "split": split, "scenarios": scenarios}
        raise NotFound(f"No judge dataset split at {split_path}")

    def remove_judge_dataset(self, set_name: str, category: str) -> None:
        folder = (self.artifacts_dir / set_name / "splits" / category).resolve()
        if self.artifacts_dir.resolve() not in folder.parents or not folder.is_dir():
            raise NotFound(f"No judge dataset {set_name}/{category}")
        shutil.rmtree(folder)

    # -- holdout -----------------------------------------------------------

    def holdout(self) -> dict[str, Any] | None:
        """The project's train/dev/test holdout of its scenarios, if made."""
        from omniagents.core.eval.holdout import HOLDOUT_SUBDIR

        manifest = self.eval_dir / HOLDOUT_SUBDIR / "manifest.yml"
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
        write_holdout(self.eval_dir, assignment, manifest)
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
        ``include_unmeasured``), saved in the evaluations directory's
        ``results/``. With one, every scenario in that set runs, saved beside
        it. Either way each run keeps its conversation, which judges and
        reviews read. Returns the saved run (:func:`results.read_run`).
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
            results_for = set_path
            measured_only = (
                False if include_unmeasured is None else not include_unmeasured
            )
        else:
            if not eval_cfg.get("scenarios"):
                raise EvalWorkspaceError("The project has no scenarios to run")
            results_for = self.eval_dir / "scenarios.yml"
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
            results_for,
            data,
            source=source,
            scenario_name=label,
            agent=self.agent_key(),
            workflow_name=data.get("workflow_name"),
        )
        return read_run(saved)

    def runs(self) -> list[dict[str, Any]]:
        """Every saved run of the project's and generated scenarios, newest first."""
        return list_runs([self.eval_dir, self.artifacts_dir])

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
