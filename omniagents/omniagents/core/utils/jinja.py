from __future__ import annotations

from pathlib import Path
from .jinja_instructions import (
    render_template as _render_template_core,
    read_text_file as _read_text_file_core,
)


def render_template(template_str: str, **kwargs) -> str:
    return _render_template_core(template_str, silent_undefined=False, **kwargs)


def read_text_file(base: Path, rel_or_abs: str) -> str:
    return _read_text_file_core(base, rel_or_abs)
