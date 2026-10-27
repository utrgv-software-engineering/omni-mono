"""A project's own values, for its text: ``{{ project.* }}``.

Agents' instructions and automations (their triggers and their instruction)
are Jinja templates with ``project`` in scope, so what names the project, its
GitHub account, the label that routes work to it, the repositories it works
in, is written once, in ``project.yml``, and a copy of the project renamed
there is renamed everywhere::

    project.name, project.label, project.slug    # product:
    project.github.account                       # integrations: [{type: github, account: ...}]
    project.github.repositories
    project.github.routing_label                 # any setting an integration has

An integration's settings are under its ``type`` (the first of a type).
Settings named like credentials (``token``, ``secret``, ``key``,
``password``, ``credential`` in the name) are left out, so no prompt carries
one; the rest have ``${VAR}`` substituted as the integrations themselves do.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Mapping

__all__ = ["project_values", "project_values_at"]

_CREDENTIAL = re.compile(r"token|secret|key|password|credential", re.I)


def project_values(raw: Mapping[str, Any], product: Any) -> Dict[str, Any]:
    """``project``'s value: the product's identity and the integrations' settings."""
    from omniagents.core.config.loader import _substitute_env_vars

    values: Dict[str, Any] = {
        "name": product.name,
        "label": product.label,
        "slug": product.slug,
    }
    for entry in raw.get("integrations") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
            continue
        kind = entry["type"]
        if kind == "github":
            from .github_settings import github_settings

            entry = github_settings(entry)
        if kind in values:
            continue
        settings: Dict[str, Any] = {}
        for name, value in entry.items():
            if name == "type" or _CREDENTIAL.search(str(name)):
                continue
            try:
                settings[name] = _substitute_env_vars(value)
            except ValueError:
                continue  # a required variable that is not set: not known here
        values[kind] = settings
    return values


def project_values_at(root: Path) -> Dict[str, Any]:
    """``project``'s value for the project at ``root`` (its ``project.yml``)."""
    from .runtime import _read_yaml, manifest_product

    raw = _read_yaml(Path(root) / "project.yml")
    return project_values(raw, manifest_product(raw, Path(root)))
