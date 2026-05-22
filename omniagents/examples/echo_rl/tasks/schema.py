"""Task schema for terminal-agent RL.

A task is the unit the agent is trained/evaluated on. It mirrors the echo-rl /
OpenThoughts-Agent task "triplet" (instruction + Docker environment + verifier),
but in a single declarative dataclass that works for both the bundled corpus
(stock image + setup commands) and dataset-derived tasks (Dockerfile + pytest).

The fields flow into an EvalSuite record:
    input  = prompt
    expect = {id, image, setup, verifier}   # become dataset columns
and from there into the environment's reset(**row) and the verifier reward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TaskSpec:
    """A single terminal task.

    Attributes:
        id: Stable task identifier.
        prompt: The instruction shown to the agent.
        image: Prebuilt Docker image tag the rollout runs in.
        setup: Shell commands run in the container before the agent acts
            (fixtures / starting state).
        verifier: Shell command run in the final container; exit code 0 == pass.
            (Dataset tasks typically use ``python -m pytest -q /tests``.)
        tags: Free-form categories (e.g. "file-ops", "data-processing").
        dockerfile: Optional Dockerfile contents. When set, loaders build it to
            an image tag (see ``tasks.images.ensure_image``) and populate ``image``.
    """

    id: str
    prompt: str
    image: str = "python:3.11-slim"
    setup: list[str] = field(default_factory=list)
    verifier: str = ""
    tags: list[str] = field(default_factory=list)
    dockerfile: Optional[str] = None
    split: str = "train"            # "train" | "val" (load-time membership)
    world_model_only: bool = False  # verifier-free: feeds ECHO only, no policy gradient

    def to_record(self) -> dict[str, Any]:
        """Row for EvalSuite.from_records (see tasks.loaders.to_eval_suite)."""
        return {
            "id": self.id,
            "prompt": self.prompt,
            "image": self.image,
            "setup": list(self.setup),
            "verifier": self.verifier,
            "tags": list(self.tags),
            "split": self.split,
            "world_model_only": self.world_model_only,
        }


def task_input(record: dict[str, Any]) -> str:
    return record["prompt"]


def task_expect(record: dict[str, Any]) -> dict[str, Any]:
    # These keys become dataset columns and reach reset(**row) + the reward.
    # NB: the image column is "docker_image", NOT "image" — TRL's GRPOTrainer
    # treats an "image"/"images" column as a vision input and rewrites text
    # message content into multimodal parts, which breaks text-only chat
    # templates. Avoid those reserved names for non-vision columns.
    return {
        "id": record["id"],
        "docker_image": record["image"],
        "setup": record["setup"],
        "verifier": record["verifier"],
        "world_model_only": record.get("world_model_only", False),
    }
