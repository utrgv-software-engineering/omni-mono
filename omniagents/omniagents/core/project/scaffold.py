from __future__ import annotations

from pathlib import Path
from typing import List, Tuple, Optional
import shutil
import yaml
from omniagents._version import __version__ as OMNIAGENTS_VERSION

TEMPLATES_DIR = Path(__file__).with_name("templates")


def _render_template(relative_path: str, **context: str) -> str:
    template_path = TEMPLATES_DIR / relative_path
    if not template_path.is_file():
        raise FileNotFoundError(f"Missing template: {template_path}")
    content = template_path.read_text(encoding="utf-8")
    for key, value in context.items():
        content = content.replace(f"{{{key}}}", value)
    return content


def _write_template(
    target_path: Path,
    template_rel_path: str,
    force: bool,
    created: List[str],
    **context: str,
) -> None:
    if target_path.exists() and not force:
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        _render_template(template_rel_path, **context), encoding="utf-8"
    )
    created.append(str(target_path))


def _sanitize_name(name: str) -> str:
    import re

    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    if not s:
        s = "agent"
    if not s[0].isalpha():
        s = f"a_{s}"
    if not re.fullmatch(r"[a-z][a-z0-9_]*", s):
        s = re.sub(r"[^a-z0-9_]", "", s)
        if not s or not s[0].isalpha():
            s = "agent"
    return s


def _titleize(s: str) -> str:
    parts = [p for p in s.split("_") if p]
    return " ".join(p.capitalize() for p in parts) or s


def _camel_case(s: str) -> str:
    parts = [p for p in s.split("_") if p]
    return "".join(p.capitalize() for p in parts)


def scaffold_project(
    project_name: str,
    parent_dir: str = ".",
    model: str = "gpt-4.1",
    force: bool = False,
    include_samples: bool = True,
) -> Tuple[List[str], str, str]:
    sanitized = _sanitize_name(project_name)
    parent = Path(parent_dir).resolve()
    project_dir = parent / sanitized
    if project_dir.exists() and not force:
        if any(project_dir.iterdir()):
            raise FileExistsError(
                f"Directory already exists and is not empty: {project_dir}"
            )
    project_dir.mkdir(parents=True, exist_ok=True)

    agents_dir = project_dir / "agents"
    tools_dir = project_dir / "tools"
    target_dir = project_dir / "target"
    docs_dir = project_dir / "docs"
    devcontainer_dir = project_dir / ".devcontainer"
    for path in (agents_dir, tools_dir, target_dir, docs_dir, devcontainer_dir):
        path.mkdir(exist_ok=True)

    created: List[str] = []

    _write_template(
        project_dir / "project.yml",
        "project/project.yml",
        force,
        created,
        sanitized=sanitized,
    )

    agent_dir = agents_dir / sanitized
    agent_dir.mkdir(parents=True, exist_ok=True)
    _write_template(
        agent_dir / "instructions.md",
        "project/agents/instructions.md",
        force,
        created,
    )

    display_label = _titleize(sanitized)
    tools_list = "[echo, word_count]" if include_samples else "[]"
    config_dir_unix = sanitized.replace("_", "-")
    config_dir_windows = _camel_case(sanitized)
    setup_prog = f"{sanitized}-setup"
    _write_template(
        agent_dir / "agent.yml",
        "project/agents/agent.yml",
        force,
        created,
        sanitized=sanitized,
        label=display_label,
        model=model,
        tools=tools_list,
    )

    if include_samples:
        _write_template(
            tools_dir / f"{sanitized}_tools.py",
            "project/tools/sample_tools.py",
            force,
            created,
        )

    _write_template(
        tools_dir / "__init__.py",
        "project/tools/__init__.py",
        force,
        created,
    )

    package_dir = project_dir / sanitized
    package_dir.mkdir(parents=True, exist_ok=True)
    _write_template(
        package_dir / "__init__.py",
        "project/package/__init__.py",
        force,
        created,
    )
    _write_template(
        package_dir / "config.py",
        "project/package/config.py",
        force,
        created,
        config_dir_unix=config_dir_unix,
        config_dir_windows=config_dir_windows,
    )
    _write_template(
        package_dir / "cli.py",
        "project/package/cli.py",
        force,
        created,
        sanitized=sanitized,
    )
    _write_template(
        package_dir / "setup.py",
        "project/package/setup.py",
        force,
        created,
        sanitized=sanitized,
        setup_prog=setup_prog,
    )
    _write_template(
        project_dir / ".env.example",
        "project/.env.example",
        force,
        created,
    )
    _write_template(
        project_dir / ".gitignore",
        "project/.gitignore",
        force,
        created,
    )
    _write_template(
        project_dir / "README.md",
        "project/README.md",
        force,
        created,
        sanitized=sanitized,
    )
    _write_template(
        project_dir / "pyproject.toml",
        "project/pyproject.toml",
        force,
        created,
        sanitized=sanitized,
        omniagents_version=OMNIAGENTS_VERSION,
    )
    _write_template(
        project_dir / "requirements.txt",
        "project/requirements.txt",
        force,
        created,
        omniagents_version=OMNIAGENTS_VERSION,
    )
    _write_template(
        project_dir / "Makefile",
        "project/Makefile",
        force,
        created,
        sanitized=sanitized,
    )
    _write_template(
        devcontainer_dir / "devcontainer.json",
        "project/.devcontainer/devcontainer.json",
        force,
        created,
        label=display_label,
    )
    _write_template(
        devcontainer_dir / "postCreate.sh",
        "project/.devcontainer/postCreate.sh",
        force,
        created,
    )
    _write_template(
        docs_dir / "RELEASE.md",
        "project/docs/RELEASE.md",
        force,
        created,
    )

    github_dir = project_dir / ".github"
    workflows_dir = github_dir / "workflows"
    github_dir.mkdir(exist_ok=True)
    workflows_dir.mkdir(parents=True, exist_ok=True)

    _write_template(
        github_dir / "pull_request_template.md",
        "project/.github/pull_request_template.md",
        force,
        created,
    )
    _write_template(
        workflows_dir / "omniagents-eval.yml",
        "project/.github/workflows/omniagents-eval.yml",
        force,
        created,
    )
    _write_template(
        workflows_dir / "publish-pypi.yml",
        "project/.github/workflows/publish-pypi.yml",
        force,
        created,
    )
    _write_template(
        workflows_dir / "publish-gemfury.yml",
        "project/.github/workflows/publish-gemfury.yml",
        force,
        created,
    )

    eval_dir = project_dir / "evaluations"
    eval_dir.mkdir(exist_ok=True)
    _write_template(
        eval_dir / "evaluation.yml",
        "project/evaluations/evaluation.yml",
        force,
        created,
    )
    _write_template(
        eval_dir / "metrics.yml",
        "project/evaluations/metrics.yml",
        force,
        created,
    )
    _write_template(
        eval_dir / "scenarios.yml",
        "project/evaluations/scenarios.yml",
        force,
        created,
    )
    _write_template(
        eval_dir / "measures.py",
        "project/evaluations/measures.py",
        force,
        created,
    )

    return created, str(project_dir), sanitized


def _resolve_project_root(project_ref: str) -> Path:
    p = Path(project_ref).resolve()
    if p.is_file() and p.name == ("project.yml"):
        return p.parent
    if p.is_dir():
        cand = p / "project.yml"
        if cand.is_file():
            return p
    raise FileNotFoundError(f"Could not find project.yml at or under: {p}")


def scaffold_tool(
    project_ref: str, tool_name: str, make_async: bool = False, force: bool = False
) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(tool_name)
    tools_dir = root / "tools"
    tools_dir.mkdir(exist_ok=True)
    file_path = tools_dir / f"{sanitized}.py"
    if file_path.exists() and not force:
        raise FileExistsError(f"File exists: {file_path}")
    created: List[str] = []
    template_name = "tools/async.py" if make_async else "tools/sync.py"
    _write_template(file_path, template_name, True, created, sanitized=sanitized)
    return created, str(root)


def scaffold_agent(
    project_ref: str, agent_name: str, model: str = "gpt-4.1", force: bool = False
) -> Tuple[List[str], str, str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(agent_name)
    agents_dir = root / "agents"
    agents_dir.mkdir(exist_ok=True)
    agent_dir = agents_dir / sanitized
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_yml = agent_dir / "agent.yml"
    instr_md = agent_dir / "instructions.md"
    created: List[str] = []
    if agent_yml.exists() and not force:
        raise FileExistsError(f"Agent YAML exists: {agent_yml}")
    if instr_md.exists() and not force:
        raise FileExistsError(f"Instructions exists: {instr_md}")

    display_label = _titleize(sanitized)
    _write_template(
        agent_yml,
        "project/agents/agent.yml",
        True,
        created,
        sanitized=sanitized,
        label=display_label,
        model=model,
        tools="[]",
    )
    _write_template(
        instr_md,
        "project/agents/instructions.md",
        True,
        created,
    )
    return created, str(root), sanitized


def delete_agent(project_ref: str, agent_key: str) -> Tuple[List[str], str, str]:
    root = _resolve_project_root(project_ref)
    agents_dir = root / "agents"
    target_dir = agents_dir / agent_key
    if not target_dir.exists() or not target_dir.is_dir():
        raise FileNotFoundError(f"Agent directory not found: {target_dir}")
    existing_keys = sorted(
        p.name
        for p in agents_dir.iterdir()
        if p.is_dir() and (p / "agent.yml").is_file()
    )
    if agent_key not in existing_keys:
        raise FileNotFoundError(f"Agent not found: {agent_key}")
    if len(existing_keys) <= 1:
        raise ValueError("Cannot delete the only agent in the project")
    shutil.rmtree(target_dir)
    removed = [str(target_dir)]
    project_yml = root / "project.yml"
    if project_yml.is_file():
        data = yaml.safe_load(project_yml.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            data = {}
        agents_cfg = data.get("agents") if isinstance(data.get("agents"), dict) else {}
        entry = agents_cfg.get("entrypoint")
        if entry == agent_key:
            for candidate in existing_keys:
                if candidate != agent_key:
                    agents_cfg["entrypoint"] = candidate
                    break
        data["agents"] = agents_cfg
        project_yml.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    return removed, str(root), agent_key


def scaffold_guardrail(
    project_ref: str,
    kind: str,
    name: str,
    make_async: bool = False,
    force: bool = False,
) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(name)
    guard_dir = root / "guardrails"
    guard_dir.mkdir(exist_ok=True)
    file_path = guard_dir / f"{sanitized}.py"
    if file_path.exists() and not force:
        raise FileExistsError(f"File exists: {file_path}")
    created: List[str] = []
    normal_kind = str(kind).strip().lower()
    if normal_kind == "input":
        template = "guardrails/input_async.py" if make_async else "guardrails/input_sync.py"
    else:
        template = "guardrails/output_async.py" if make_async else "guardrails/output_sync.py"
    _write_template(file_path, template, True, created, sanitized=sanitized)
    return created, str(root)


def scaffold_server_function(
    project_ref: str, name: str, make_async: bool = False, force: bool = False
) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(name)
    sf_dir = root / "server_functions"
    sf_dir.mkdir(exist_ok=True)
    file_path = sf_dir / f"{sanitized}.py"
    if file_path.exists() and not force:
        raise FileExistsError(f"File exists: {file_path}")
    created: List[str] = []
    template = (
        "server_functions/async.py" if make_async else "server_functions/sync.py"
    )
    _write_template(file_path, template, True, created, sanitized=sanitized)
    return created, str(root)


def scaffold_judge(
    project_ref: str,
    key: str,
    model: str = "gpt-4.1",
    source: str = "final_assistant_text",
    add_measure: bool = True,
    failure_category: Optional[str] = None,
    force: bool = False,
) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(key)
    eval_dir = root / "evaluations"
    eval_dir.mkdir(exist_ok=True)
    judges_root = eval_dir / "judges" / sanitized
    judges_root.mkdir(parents=True, exist_ok=True)
    created: List[str] = []

    judge_yml = judges_root / "judge.yml"
    if judge_yml.exists() and not force:
        raise FileExistsError(f"Judge exists: {judge_yml}")

    agent_name = _camel_case(sanitized)
    agent_label = _titleize(sanitized)

    _write_template(
        judge_yml,
        "judges/judge.yml",
        True,
        created,
        sanitized=sanitized,
        agent_name=agent_name,
        agent_label=agent_label,
        model=model,
        source=source,
    )

    judge_instructions_md = judges_root / "instructions.md"
    if force or not judge_instructions_md.exists():
        _write_template(
            judge_instructions_md,
            "judges/instructions.md",
            True,
            created,
        )

    judge_template_md = judges_root / "template.md"
    if force or not judge_template_md.exists():
        _write_template(
            judge_template_md,
            "judges/template.md",
            True,
            created,
        )

    if add_measure:
        measures_py = eval_dir / "measures.py"
        fn_name = f"{sanitized}_agent"
        block = _render_template(
            "judges/measure_block.py",
            function_name=fn_name,
            sanitized=sanitized,
        )
        import_line = (
            "from omniagents.core.evaluation import evaluation_measure, fail_reason"
        )
        if measures_py.exists():
            existing = measures_py.read_text(encoding="utf-8")
            if f"def {fn_name}(" not in existing:
                import re

                updated = existing
                pattern = re.compile(
                    r"from omniagents\\.core\\.evaluation import (?P<body>\([^\)]*\)|[^\n]*)",
                    re.DOTALL,
                )
                match = pattern.search(updated)
                need_import = match is None
                if match:
                    body = match.group("body")
                    cleaned = body.replace("(", "").replace(")", "").replace("\n", " ")
                    parts = [part.strip() for part in cleaned.split(",")]
                    names = [part for part in parts if part]
                    changed = False
                    for required in ("evaluation_measure", "fail_reason"):
                        if required not in names:
                            names.append(required)
                            changed = True
                    if changed:
                        if body.strip().startswith("("):
                            indent = "    "
                            inner = ",\n".join(f"{indent}{name}" for name in names) + ","
                            new_body = f"(\n{inner}\n)"
                        else:
                            new_body = ", ".join(names)
                        new_line = (
                            f"from omniagents.core.evaluation import {new_body}"
                        )
                        updated = (
                            updated[: match.start()] + new_line + updated[match.end() :]
                        )
                    need_import = False
                if need_import:
                    doc_pattern = re.compile(r'(\s*("""|\'\'\').*?\2\s*)', re.DOTALL)
                    doc_match = doc_pattern.match(updated)
                    if doc_match:
                        start = doc_match.end()
                        updated = (
                            updated[:start]
                            + "\n"
                            + import_line
                            + "\n\n"
                            + updated[start:]
                        )
                    else:
                        trimmed = updated.lstrip("\n")
                        prefix = "\n" if updated.startswith("\n") else ""
                        updated = prefix + import_line + "\n\n" + trimmed
                updated = updated.rstrip()
                updated = (updated + "\n\n" if updated else "") + block + "\n"
                measures_py.write_text(updated, encoding="utf-8")
                created.append(str(measures_py))
        else:
            measures_py.write_text(f"{import_line}\n\n{block}\n", encoding="utf-8")
            created.append(str(measures_py))

    if failure_category:
        evaluation_yml = eval_dir / "evaluation.yml"
        base = {}
        if evaluation_yml.exists():
            try:
                base = yaml.safe_load(evaluation_yml.read_text(encoding="utf-8")) or {}
            except Exception:
                base = {}
        if not isinstance(base, dict):
            base = {}
        ev = base.get("evaluation") or {}
        if not isinstance(ev, dict):
            ev = {}
        fcs = ev.get("failure_categories") or []
        if not isinstance(fcs, list):
            fcs = []
        found = None
        for it in fcs:
            if isinstance(it, dict) and str(it.get("name")) == str(failure_category):
                found = it
                break
        if found is None:
            found = {"name": str(failure_category), "measures": [f"{sanitized}_agent"]}
            fcs.append(found)
        else:
            ms = found.get("measures") or []
            if f"{sanitized}_agent" not in ms:
                ms.append(f"{sanitized}_agent")
                found["measures"] = ms
        ev["failure_categories"] = fcs
        base["evaluation"] = ev
        evaluation_yml.write_text(
            yaml.safe_dump(base, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        created.append(str(evaluation_yml))

    return created, str(root)


def scaffold_optimizer(
    project_ref: str,
    key: str,
    model: str = "gpt-4.1",
    kind: str = "agent",
    force: bool = False,
) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(key)
    eval_dir = root / "evaluations"
    eval_dir.mkdir(exist_ok=True)
    opt_root = eval_dir / "optimizers" / sanitized
    opt_root.mkdir(parents=True, exist_ok=True)
    created: List[str] = []

    optimizer_yml = opt_root / "optimizer.yml"
    if optimizer_yml.exists() and not force:
        raise FileExistsError(f"Optimizer exists: {optimizer_yml}")

    agent_name = _camel_case(sanitized)

    _write_template(
        optimizer_yml,
        "optimizers/optimizer.yml",
        True,
        created,
        sanitized=sanitized,
        agent_name=agent_name,
        model=model,
    )

    instructions_template = (
        "optimizers/instructions_judge.md"
        if str(kind).strip().lower() == "judge"
        else "optimizers/instructions_agent.md"
    )
    rewrite_instructions_template = (
        "optimizers/rewrite_instructions_judge.md"
        if str(kind).strip().lower() == "judge"
        else "optimizers/rewrite_instructions_agent.md"
    )

    instructions_path = opt_root / "instructions.md"
    if force or not instructions_path.exists():
        _write_template(instructions_path, instructions_template, True, created)

    rewrite_instructions_path = opt_root / "rewrite_instructions.md"
    if force or not rewrite_instructions_path.exists():
        _write_template(
            rewrite_instructions_path,
            rewrite_instructions_template,
            True,
            created,
        )

    rewrite_template_path = opt_root / "rewrite_template.md"
    if force or not rewrite_template_path.exists():
        _write_template(
            rewrite_template_path,
            "optimizers/rewrite_template.md",
            True,
            created,
        )

    return created, str(root)
