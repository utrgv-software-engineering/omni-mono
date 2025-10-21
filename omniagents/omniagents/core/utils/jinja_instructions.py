"""Jinja2 template utilities for both instructions and evaluation templates."""

from typing import Any, Dict
from dataclasses import is_dataclass, asdict
from pathlib import Path
import json
from jinja2 import Environment
from agents.run_context import RunContextWrapper
from agents import Agent


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
    if is_dataclass(context):
        return asdict(context)

    # Try to get __dict__ for plain objects
    if hasattr(context, "__dict__"):
        return vars(context)

    # Last resort: create a dict with the context as a single key
    return {"context": context}


def _create_env(*, silent_undefined: bool = False) -> Environment:
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
    return env


def render_template(
    template_str: str, /, *, silent_undefined: bool = False, **kwargs
) -> str:
    try:
        env = _create_env(silent_undefined=silent_undefined)
        tmpl = env.from_string(template_str)
        return str(tmpl.render(**kwargs))
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


def create_jinja2_instructions(template_string: str):
    env = _create_env(silent_undefined=True)
    template = env.from_string(template_string)

    async def render_instructions(
        context_wrapper: RunContextWrapper, agent: Agent
    ) -> str:
        context = context_wrapper.context if context_wrapper else None
        context_dict = context_to_dict(context)
        rendered = template.render(context=context, **context_dict)
        return rendered

    return render_instructions


def process_instructions(instructions: Any) -> Any:
    """Process instructions, treating strings as Jinja2 and wrapping callables to process their output.

    Args:
        instructions: String (treated as Jinja2), callable (output treated as Jinja2), or None

    Returns:
        Wrapped function that processes Jinja2, or None if instructions is None
    """
    if instructions is None:
        return None

    if isinstance(instructions, str):
        # Treat strings as Jinja2 templates
        return create_jinja2_instructions(instructions)

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
                renderer = create_jinja2_instructions(result)
                # Render the template with the context
                return await renderer(context_wrapper, agent)

            # If not a string, return as-is
            return result

        return wrapped_instructions

    # For any other type, return as-is
    return instructions
