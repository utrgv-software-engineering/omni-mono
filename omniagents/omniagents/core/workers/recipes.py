"""Versioned reconstruction of builtin worker adapters, never pickled closures."""

from __future__ import annotations


def reconstruct(recipe, service, parent_id):
    from .config import worker_defaults

    if (
        recipe.get("version") != 1
        or recipe.get("definition") != worker_defaults().recipe_id
    ):
        raise ValueError(
            "Worker definition changed or is unavailable; recipe migration is required"
        )
    kind = recipe.get("kind")
    if kind == "project":
        return None
    from omniagents.builtin.tools import swarm_tools

    if kind == "single":
        return swarm_tools._validate_worker_spec
    if kind == "swarm":
        swarm_tools.restore_groups(service, parent_id)
        swarm = swarm_tools._SWARMS.get(recipe["swarm_id"])
        if swarm is None or swarm.owner != parent_id or swarm.closed:
            raise ValueError("Worker swarm is unavailable or closed")
        member = swarm_tools._member(swarm, recipe["member"])
        return swarm_tools._configure_member(swarm, member)
    raise ValueError("Unknown worker reconstruction recipe")
