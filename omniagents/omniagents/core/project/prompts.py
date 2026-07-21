"""Product-declared prompt template files.

Products ship prompt text as data: Markdown files in a directory the
manifest names — ``paths: {prompts: <dir>}``, manifest-relative, default
``prompts`` — instead of Python modules of f-strings. Conventional
filenames bind each file to a framework seam:

- ``goal_initial.md`` — the first user message of a ``/goal`` autopilot
  run. Jinja variables: ``goal`` (str), ``max_turns`` (int).
- ``goal_tick.md`` — the periodic goal tick notification body. Jinja
  variables: ``goal`` (str), ``max_turns`` (int).
- ``loop.md`` — the default ``/loop`` prompt body used when ``/loop`` is
  invoked without prompt text (the last fallback of the override chain
  in ``omniagents.core.autonomy.loop``). No variables.

Templates render through the same Jinja environment as agent
instructions (:func:`omniagents.core.utils.jinja_instructions.render_template`,
silent-undefined) and the rendered output is stripped — matching the
``.strip()`` the code-owned builders applied, so moving a prompt from an
f-string to a file is byte-preserving.

Precedence at each seam: an explicitly installed builder/resolver
(``set_goal_prompt_builders`` / ``set_loop_default_prompt_resolver``)
always wins; otherwise a product template file (if present) wins over
the framework's generic default wording.

Like ``resolve_project_agent_yaml``, resolution is a light manifest read
(only ``paths.prompts`` is consulted; no discovery, no overlays) against
an explicit ``project_path`` or the installed product identity's
manifest (``product_info().manifest_path``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from omniagents.core.utils.prompt_files import (
    DEFAULT_MAX_PROMPT_FILE_BYTES,
    resolve_prompt_file,
)

__all__ = [
    "product_prompts_dirs",
    "product_prompt_template",
    "render_prompt_template",
]

DEFAULT_PROMPTS_DIRNAME = "prompts"


def product_prompts_dirs(project_path: Any = None) -> List[Path]:
    """The prompt-template directories a project manifest declares.

    *project_path* is a project.yml path or a directory containing one;
    when omitted, the installed product identity's manifest is used
    (``product_info().manifest_path``). Returns ``[]`` when there is no
    manifest to resolve against. ``paths.prompts`` may be a string or a
    list (manifest-relative or absolute); when absent, the conventional
    ``prompts/`` sibling of the manifest applies.
    """
    import yaml

    if project_path is None:
        from omniagents.core.product import product_info

        project_path = product_info().manifest_path
    if project_path is None:
        return []
    manifest = Path(project_path).expanduser()
    if manifest.is_dir():
        manifest = manifest / "project.yml"
    if not manifest.is_file():
        return []
    base_dir = manifest.parent

    try:
        proj = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except Exception:
        proj = {}
    paths_cfg = proj.get("paths") if isinstance(proj, dict) else None
    prompts_cfg: Any = DEFAULT_PROMPTS_DIRNAME
    if isinstance(paths_cfg, dict):
        prompts_cfg = paths_cfg.get("prompts", DEFAULT_PROMPTS_DIRNAME)
    if isinstance(prompts_cfg, str):
        entries = [prompts_cfg]
    elif isinstance(prompts_cfg, list):
        entries = [str(x) for x in prompts_cfg]
    else:
        entries = [DEFAULT_PROMPTS_DIRNAME]

    out: List[Path] = []
    for entry in entries:
        d = Path(entry)
        if not d.is_absolute():
            d = (base_dir / entry).resolve()
        out.append(d)
    return out


def product_prompt_template(
    name: str,
    project_path: Any = None,
    *,
    max_bytes: int = DEFAULT_MAX_PROMPT_FILE_BYTES,
) -> Optional[str]:
    """The text of the product's packaged prompt template *name*, or None.

    Tries ``<prompts-dir>/<name>`` across the declared prompt dirs in
    order; the first existing file with non-whitespace content wins
    (size-capped, stripped — :func:`resolve_prompt_file` semantics).
    Returns ``None`` when no manifest is installed or no template file
    exists, so callers fall through to their code-owned default.
    """
    try:
        dirs = product_prompts_dirs(project_path)
    except Exception:
        return None
    if not dirs:
        return None
    text = resolve_prompt_file(
        [d / name for d in dirs], "", max_bytes=max_bytes
    )
    return text or None


def render_prompt_template(text: str, **context: Any) -> str:
    """Render *text* with the instructions Jinja environment and strip.

    Same environment as agent instructions (silent-undefined, ``tojson``
    filter); the strip matches the ``.strip()`` code-owned prompt
    builders apply, so template files may end with a trailing newline
    without changing the rendered prompt.
    """
    from omniagents.core.utils.jinja_instructions import render_template

    return render_template(text, silent_undefined=True, **context).strip()
