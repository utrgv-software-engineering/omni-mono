from pathlib import Path

from .parser import find_skill_md
from .prompt import to_prompt
from .validator import validate


def discover_skill_dirs(skill_roots: list[Path]) -> list[Path]:
    candidates: list[Path] = []
    for root in skill_roots:
        try:
            resolved_root = Path(root)
            if not resolved_root.exists() or not resolved_root.is_dir():
                continue
        except Exception:
            continue

        if find_skill_md(resolved_root) is not None:
            candidates.append(resolved_root)

        try:
            children = sorted(resolved_root.iterdir(), key=lambda entry: entry.name)
        except Exception:
            continue

        for child in children:
            if not child.is_dir():
                continue
            if find_skill_md(child) is not None:
                candidates.append(child)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except Exception:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    return unique


def merge_skill_dirs(skill_dirs: list[Path]) -> list[Path]:
    merged: list[Path] = []
    seen_names: set[str] = set()
    for candidate in skill_dirs:
        name = Path(candidate).name
        if name in seen_names:
            continue
        seen_names.add(name)
        merged.append(Path(candidate))
    return merged


def build_available_skills_block(skill_roots: list[Path]) -> str:
    discovered = discover_skill_dirs(skill_roots)
    valid = [skill_dir for skill_dir in discovered if not validate(skill_dir)]
    return to_prompt(merge_skill_dirs(valid))


def read_skill_text(skill_dir: Path) -> str | None:
    """The SKILL.md body for a discovered skill directory, or None when
    the directory has none. Reads happen here, in the skills package that
    already owns skill discovery, so server functions never touch host
    paths themselves."""
    from .parser import find_skill_md

    path = find_skill_md(Path(skill_dir))
    if path is None:
        return None
    return path.read_text(encoding="utf-8")
