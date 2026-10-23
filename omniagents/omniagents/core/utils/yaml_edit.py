"""Editing a YAML file people also edit by hand.

A file such as ``agent.yml`` or ``project.yml`` carries comments and a
layout its authors chose. Loading it to plain data and dumping it back
discards both, so programs that change one setting go through here: the
document is parsed round-trip and written back with the file's own
indentation, so only the edited keys change.
"""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.util import load_yaml_guess_indent


def _mapping_indent(text: str) -> int | None:
    """How far a nested mapping is indented, from the first one in ``text``."""
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for parent, child in zip(lines, lines[1:]):
        opens_block = parent.split("#", 1)[0].rstrip().endswith(":")
        depth = len(child) - len(child.lstrip())
        parent_depth = len(parent) - len(parent.lstrip())
        if opens_block and depth > parent_depth and not child.lstrip().startswith("-"):
            return depth - parent_depth
    return None


def load_document(text: str, *, sequence: bool = False) -> tuple[YAML, Any]:
    """``text`` as an editable mapping (or, with ``sequence``, a mapping or
    a list), and the YAML that writes it back in the file's own layout.
    Raises ``TypeError`` for any other document."""
    # ruamel's guess describes block sequences: where an item's dash sits
    # (offset) and where its content starts (indent).
    _, sequence_indent, dash_offset = load_yaml_guess_indent(text)
    mapping = _mapping_indent(text) or 2
    if dash_offset is None:
        sequence_indent, dash_offset = mapping + 2, mapping
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=mapping, sequence=sequence_indent, offset=dash_offset)
    # Parsed again by the YAML that writes it back: only its own parse
    # keeps the quoting.
    data = yaml.load(text)
    if data is None:
        data = CommentedMap()
    allowed = (CommentedMap, CommentedSeq) if sequence else (CommentedMap,)
    if not isinstance(data, allowed):
        raise TypeError(
            "expected a mapping or a list" if sequence else "expected a mapping of settings"
        )
    return yaml, data


def dump_document(yaml: YAML, data: Any) -> str:
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    return buffer.getvalue()


def merge_into(target: Any, value: Any) -> Any:
    """``value`` written over ``target`` in place where both are mappings,
    so comments on the keys the edit kept survive; otherwise ``value``."""
    if isinstance(target, CommentedMap) and isinstance(value, Mapping):
        for key in [k for k in target if k not in value]:
            del target[key]
        for key, item in value.items():
            target[key] = merge_into(target[key], item) if key in target else item
        return target
    return value


def replace_file(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` in one step: a reader sees the old
    file or the new one, never part of either."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(name, path)
    except BaseException:
        if os.path.exists(name):
            os.unlink(name)
        raise
