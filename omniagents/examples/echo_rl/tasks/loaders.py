"""Load terminal tasks from three sources into a common ``TaskSpec`` list.

  * ``load_builtin``         — the bundled YAML corpus (no download; laptop/CI).
  * ``load_openthoughts_rl`` — the public HF dataset (echo-rl's lineage).
  * ``load_echo_parquet``    — echo-rl's own parquet files (if you have them).

The HF dataset and echo-rl parquets share one schema: rows of ``path`` (str) +
``task_binary`` (a tar of {instruction markdown, Dockerfile, pytest tests}), so
they share one extractor, :func:`task_spec_from_binary`.

``to_eval_suite`` turns any ``list[TaskSpec]`` into an ``EvalSuite`` ready for
training (``train_grpo``) or evaluation.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from .images import ensure_image
from .schema import TaskSpec, task_expect, task_input

BUILTIN_DIR = Path(__file__).parent / "builtin"
DEFAULT_VERIFIER = "python -m pytest -q /tests"


# --------------------------------------------------------------------------- #
# Bundled corpus
# --------------------------------------------------------------------------- #
def load_builtin(
    categories: Optional[Iterable[str]] = None,
    *,
    split: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[TaskSpec]:
    """Load the bundled YAML tasks, optionally filtered by tag and/or split.

    Args:
        categories: keep only tasks whose tags intersect these.
        split: keep only tasks with this ``split`` ("train" / "val"); None = all.
        limit: cap the number of tasks returned.
    """
    want = set(categories) if categories else None
    tasks: list[TaskSpec] = []
    for path in sorted(BUILTIN_DIR.glob("*.yaml")):
        for row in yaml.safe_load(path.read_text()) or []:
            spec = TaskSpec(**row)
            if want is not None and not want.intersection(spec.tags):
                continue
            if split is not None and spec.split != split:
                continue
            tasks.append(spec)
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


# --------------------------------------------------------------------------- #
# task_binary extractor (shared by HF + parquet)
# --------------------------------------------------------------------------- #
def task_spec_from_binary(
    path: str,
    blob: bytes,
    *,
    build_images: bool = True,
    verifier_cmd: str = DEFAULT_VERIFIER,
) -> TaskSpec:
    """Turn a ``(path, task_binary)`` row into a ``TaskSpec``.

    Expects ``task_binary`` to be a tar containing a Dockerfile, an instruction
    markdown file, and a pytest test tree (the standard OpenThoughts/echo-rl
    "triplet"). The Dockerfile is built to an image (cached) that is assumed to
    bake in the task fixtures and tests; the verifier runs pytest in the image.

    NOTE: the exact member layout can vary by dataset release. This extractor is
    deliberately tolerant (it scans member names); if a particular release
    differs, override ``verifier_cmd`` or adjust the name heuristics below. The
    dataset ships an ``extract_parquet_tasks.py`` you can use to confirm layout.
    """
    dockerfile: Optional[str] = None
    instruction: Optional[str] = None
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            name = m.name.rsplit("/", 1)[-1].lower()
            data = tar.extractfile(m).read().decode("utf-8", errors="replace")
            if name == "dockerfile":
                dockerfile = data
            elif name.endswith(".md") and instruction is None:
                instruction = data
            elif name in ("instruction.txt", "task.txt") and instruction is None:
                instruction = data

    if instruction is None:
        instruction = f"Complete the task described in {path}."
    image = "python:3.11-slim"
    if dockerfile and build_images:
        image = ensure_image(dockerfile)

    return TaskSpec(
        id=path,
        prompt=instruction.strip(),
        image=image,
        verifier=verifier_cmd,
        tags=["dataset"],
        dockerfile=dockerfile,
    )


def _load_parquet_rows(data_files: Any) -> list[dict]:
    from datasets import load_dataset

    ds = load_dataset("parquet", data_files=data_files, split="train")
    return list(ds)


# --------------------------------------------------------------------------- #
# OpenThoughts-Agent-v1-RL (HuggingFace)
# --------------------------------------------------------------------------- #
def load_openthoughts_rl(
    *,
    split: str = "train",
    limit: Optional[int] = None,
    build_images: bool = True,
    repo_id: str = "open-thoughts/OpenThoughts-Agent-v1-RL",
) -> list[TaskSpec]:
    """Load tasks from the public OpenThoughts-Agent-v1-RL dataset (~720 tasks).

    Requires network + ``pip install datasets`` and Docker (to build per-task
    images when ``build_images=True``). This is echo-rl's actual task lineage.
    """
    from datasets import load_dataset

    ds = load_dataset(repo_id, split=split)
    if limit is not None:
        ds = ds.select(range(min(limit, len(ds))))
    return [
        task_spec_from_binary(
            row["path"], row["task_binary"], build_images=build_images
        )
        for row in ds
    ]


# --------------------------------------------------------------------------- #
# echo-rl parquet files (bring-your-own)
# --------------------------------------------------------------------------- #
def load_echo_parquet(
    data_files: str | list[str],
    *,
    limit: Optional[int] = None,
    build_images: bool = True,
) -> list[TaskSpec]:
    """Load tasks from echo-rl's own parquet files (path + task_binary schema)."""
    rows = _load_parquet_rows(data_files)
    if limit is not None:
        rows = rows[:limit]
    return [
        task_spec_from_binary(r["path"], r["task_binary"], build_images=build_images)
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# -> EvalSuite
# --------------------------------------------------------------------------- #
def to_eval_suite(tasks: list[TaskSpec], *, name: str = "terminal_tasks"):
    """Build an EvalSuite from TaskSpecs (used for both training and eval)."""
    from omniagents.notebook import EvalSuite

    records = [t.to_record() for t in tasks]
    return EvalSuite.from_records(
        records,
        name=name,
        input_fn=task_input,
        expect_fn=task_expect,
    )
