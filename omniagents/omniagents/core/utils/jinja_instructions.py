"""Jinja2 template utilities for both instructions and evaluation templates.

Instruction templates support ``{% include %}`` of shared fragments when the
caller provides ``search_paths`` (directories searched, in order, for the
included template name). The YAML loader computes these from the instructions
file's own directory plus the shared-fragment conventions — see
:func:`instruction_search_paths`. When no search paths are provided the
environment has no loader and rendering behaves exactly as before (includes
are unsupported and raise a clear error).
"""

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union
from dataclasses import is_dataclass, asdict
from pathlib import Path
import json
from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    TemplateNotFound,
)
from agents.run_context import RunContextWrapper
from agents import Agent

PathLike = Union[str, Path]


class InstructionIncludeNotFoundError(Exception):
    """An ``{% include %}`` in an instruction template could not be resolved."""

    def __init__(self, template_name: str, search_paths: Sequence[PathLike]):
        self.template_name = template_name
        self.search_paths = [str(p) for p in search_paths]
        if self.search_paths:
            detail = "Searched (in order): " + ", ".join(self.search_paths)
        else:
            detail = (
                "No include search paths are configured for this template "
                "(instructions were not loaded from a file, so there is no "
                "directory to resolve includes against)."
            )
        super().__init__(f"Instruction include {template_name!r} not found. {detail}")


def instruction_search_paths(
    anchor_dir: PathLike, extra_dirs: Iterable[PathLike] = ()
) -> List[Path]:
    """Compute the ``{% include %}`` search paths for an instructions file.

    Given the directory that contains the instructions file (``anchor_dir``),
    returns, in resolution order:

    1. ``anchor_dir`` itself — plain relative includes,
    2. ``anchor_dir/_shared`` — fragments shared by templates in that dir,
    3. ``anchor_dir/../_shared`` — the ``agents/_shared/`` sibling convention
       for the strict ``agents/<key>/`` project layout,
    4. any ``extra_dirs`` (e.g. ``paths.instruction_fragments`` from
       project.yml), in the given order.

    Duplicates are removed while preserving order. Directories need not exist;
    non-existent entries are simply skipped by the template loader.
    """
    anchor = Path(anchor_dir)
    candidates: List[Path] = [
        anchor,
        anchor / "_shared",
        anchor.parent / "_shared",
        *[Path(d) for d in extra_dirs],
    ]
    out: List[Path] = []
    seen: set = set()
    for p in candidates:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _build_loader(search_paths: Optional[Sequence[PathLike]]):
    """Build a ChoiceLoader over the given directories, or None."""
    if not search_paths:
        return None
    return ChoiceLoader([FileSystemLoader(str(p)) for p in search_paths])


def context_to_dict(context: Any) -> Dict[str, Any]:
    """Convert various context types to a dictionary for Jinja2 rendering.

    Handles:
    - dict: Used as-is
    - dataclass: Converted via asdict()
    - Pydantic models: Converted via model_dump()
    - Plain objects: Converted via vars()
    - None: Returns empty dict
    """
    if context is None:
        return {}

    if isinstance(context, dict):
        return context

    # Check for Pydantic model (without importing pydantic)
    if hasattr(context, "model_dump"):
        return context.model_dump()
    elif hasattr(context, "dict"):  # Pydantic v1 compatibility
        return context.dict()

    # Handle dataclasses
    if is_dataclass(context) and not isinstance(context, type):
        return asdict(context)

    # Try to get __dict__ for plain objects
    if hasattr(context, "__dict__"):
        return vars(context)

    # Last resort: create a dict with the context as a single key
    return {"context": context}


def _create_env(
    *,
    silent_undefined: bool = False,
    search_paths: Optional[Sequence[PathLike]] = None,
) -> Environment:
    if silent_undefined:
        from jinja2 import Undefined

        class SilentUndefined(Undefined):
            def _fail_with_undefined_error(self, *args, **kwargs):
                return ""

            __str__ = lambda self: ""
            __call__ = lambda self, *args, **kwargs: self
            __getattr__ = lambda self, name: self

        env = Environment(undefined=SilentUndefined)
    else:
        env = Environment()

    def _tojson(value, indent=None, sort_keys=False):
        return json.dumps(
            value, ensure_ascii=False, indent=indent, sort_keys=bool(sort_keys)
        )

    env.filters["tojson"] = _tojson

    # Attach the include loader after construction so the no-search-paths
    # environment is built exactly as before (byte-identical rendering for
    # templates without includes).
    loader = _build_loader(search_paths)
    if loader is not None:
        env.loader = loader
    return env


def render_template(
    template_str: str,
    /,
    *,
    silent_undefined: bool = False,
    search_paths: Optional[Sequence[PathLike]] = None,
    **kwargs,
) -> str:
    try:
        env = _create_env(silent_undefined=silent_undefined, search_paths=search_paths)
        tmpl = env.from_string(template_str)
        return str(tmpl.render(**kwargs))
    except TemplateNotFound as e:
        if search_paths:
            raise InstructionIncludeNotFoundError(str(e.name), search_paths) from e
        return template_str
    except Exception:
        return template_str


def read_text_file(base: Path, rel_or_abs: str) -> str:
    p = Path(rel_or_abs)
    if not p.is_file():
        p = (base / rel_or_abs).resolve()
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except Exception:
        return ""


def create_jinja2_instructions(
    template_string: str,
    *,
    search_paths: Optional[Sequence[PathLike]] = None,
    globals: Optional[Mapping[str, Any]] = None,
):
    env = _create_env(silent_undefined=True, search_paths=search_paths)
    # Under the run's context: a context value of the same name wins.
    env.globals.update(globals or {})
    template = env.from_string(template_string)

    async def render_instructions(
        context_wrapper: RunContextWrapper, agent: Agent
    ) -> str:
        context = context_wrapper.context if context_wrapper else None
        context_dict = context_to_dict(context)
        try:
            rendered = template.render(context=context, **context_dict)
        except TemplateNotFound as e:
            raise InstructionIncludeNotFoundError(
                str(e.name), search_paths or []
            ) from e
        except TypeError as e:
            # Jinja raises TypeError("no loader for this environment
            # specified") when a template uses {% include %} but no
            # search_paths were provided (e.g. instructions built in Python
            # rather than loaded from a file). Surface a clearer error.
            if "no loader" in str(e):
                raise InstructionIncludeNotFoundError("<unknown>", []) from e
            raise
        return rendered

    return render_instructions


def process_instructions(
    instructions: Any,
    *,
    search_paths: Optional[Sequence[PathLike]] = None,
    globals: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Process instructions, treating strings as Jinja2 and wrapping callables to process their output.

    Args:
        instructions: String (treated as Jinja2), callable (output treated as Jinja2), or None
        search_paths: Optional directories searched, in order, for
            ``{% include %}`` fragments (see :func:`instruction_search_paths`).
        globals: Names in scope for every render (``project``), under the
            run's context.

    Returns:
        Wrapped function that processes Jinja2, or None if instructions is None
    """
    if instructions is None:
        return None

    if isinstance(instructions, str):
        # Treat strings as Jinja2 templates
        return create_jinja2_instructions(
            instructions, search_paths=search_paths, globals=globals
        )

    if callable(instructions):
        # Wrap the callable to process its output as Jinja2
        import inspect

        async def wrapped_instructions(
            context_wrapper: RunContextWrapper, agent: Agent
        ) -> str:
            # Call the original function
            result = instructions(context_wrapper, agent)

            # Handle async functions
            if inspect.isawaitable(result):
                result = await result

            # If the result is a string, process it as Jinja2
            if isinstance(result, str):
                # Create a Jinja2 renderer for the result
                renderer = create_jinja2_instructions(
                    result, search_paths=search_paths, globals=globals
                )
                # Render the template with the context
                return await renderer(context_wrapper, agent)

            # If not a string, return as-is
            return result

        return wrapped_instructions

    # For any other type, return as-is
    return instructions
