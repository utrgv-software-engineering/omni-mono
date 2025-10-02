from __future__ import annotations

from pathlib import Path
from typing import List, Tuple, Optional
import yaml


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


def scaffold_project(project_name: str, parent_dir: str = ".", model: str = "gpt-4.1", force: bool = False, include_samples: bool = True) -> Tuple[List[str], str, str]:
    sanitized = _sanitize_name(project_name)
    parent = Path(parent_dir).resolve()
    project_dir = (parent / sanitized)
    if project_dir.exists() and not force:
        if any(project_dir.iterdir()):
            raise FileExistsError(f"Directory already exists and is not empty: {project_dir}")
    project_dir.mkdir(parents=True, exist_ok=True)

    agents_dir = project_dir / "agents"
    tools_dir = project_dir / "tools"
    target_dir = project_dir / "target"
    agents_dir.mkdir(exist_ok=True)
    tools_dir.mkdir(exist_ok=True)
    target_dir.mkdir(exist_ok=True)

    created: List[str] = []

    project_yml = project_dir / "project.yml"
    if not project_yml.exists() or force:
        content = (
            f"name: {sanitized}\n"
            "paths:\n"
            "  agents: agents\n"
            "  tools: tools\n"
            "  target: target\n"
            "agents:\n"
            f"  entrypoint: {sanitized}\n\n"
            "tracing:\n"
            "  provider: agentio\n"
            f"  project: {sanitized}_project\n"
            f"  name: {sanitized}\n"
            "  settings:\n"
            "    resume: never\n"
        )
        project_yml.write_text(content, encoding="utf-8")
        created.append(str(project_yml))

    instructions_dir = project_dir / "instructions"
    instructions_dir.mkdir(exist_ok=True)
    instructions_md = instructions_dir / f"{sanitized}.md"
    if not instructions_md.exists() or force:
        instructions_md.write_text(
            "You are a helpful assistant. Be concise and accurate.",
            encoding="utf-8",
        )
        created.append(str(instructions_md))

    agent_yml = agents_dir / f"{sanitized}.yml"
    if not agent_yml.exists() or force:
        def _titleize(s: str) -> str:
            parts = [p for p in s.split("_") if p]
            return " ".join(p.capitalize() for p in parts) or s
        display_label = _titleize(sanitized)
        tools_list = "[echo, word_count]" if include_samples else "[]"
        agent_content = (
            f"name: {sanitized}\n"
            f"label: {display_label}\n"
            f"description: \"A helpful AI agent. Customize this description.\"\n"
            f"model: {model}\n"
            f"instructions_file: ../instructions/{sanitized}.md\n"
            f"welcome_text: \"Hello from {sanitized}.\"\n\n"
            f"tools: {tools_list}\n"
        )
        agent_yml.write_text(agent_content, encoding="utf-8")
        created.append(str(agent_yml))

    if include_samples:
        tools_py = tools_dir / f"{sanitized}_tools.py"
        if not tools_py.exists() or force:
            tools_content = (
                "from omniagents import function_tool\n\n"
                "@function_tool\n"
                "def echo(text: str) -> str:\n"
                "    return text\n\n"
                "@function_tool\n"
                "def word_count(text: str) -> int:\n"
                "    return len(text.split()) if text else 0\n"
            )
            tools_py.write_text(tools_content, encoding="utf-8")
            created.append(str(tools_py))

    env_example = project_dir / ".env.example"
    if not env_example.exists() or force:
        env_example.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
        created.append(str(env_example))

    gitignore = project_dir / ".gitignore"
    if not gitignore.exists() or force:
        gitignore.write_text(
            "__pycache__/\n*.pyc\n.env\ntarget/\n.pytest_cache/\nartifacts/\n", encoding="utf-8"
        )
        created.append(str(gitignore))

    readme = project_dir / "README.md"
    if not readme.exists() or force:
        readme.write_text(
            (
                f"# {sanitized}\n\n"
                "Run GUI:\n\n"
                "```bash\n"
                "omniagents run -P project.yml\n"
                "```\n\n"
                "Add tools by editing agents YAML and tools/*.py.\n"
            ),
            encoding="utf-8",
        )
        created.append(str(readme))

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


def scaffold_tool(project_ref: str, tool_name: str, make_async: bool = False, force: bool = False) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(tool_name)
    tools_dir = root / "tools"
    tools_dir.mkdir(exist_ok=True)
    file_path = tools_dir / f"{sanitized}.py"
    if file_path.exists() and not force:
        raise FileExistsError(f"File exists: {file_path}")
    if make_async:
        body = (
            "from omniagents import function_tool\n\n"
            f"@function_tool\n"
            f"async def {sanitized}(text: str) -> str:\n"
            f"    return text\n"
        )
    else:
        body = (
            "from omniagents import function_tool\n\n"
            f"@function_tool\n"
            f"def {sanitized}(text: str) -> str:\n"
            f"    return text\n"
        )
    file_path.write_text(body, encoding="utf-8")
    return [str(file_path)], str(root)


def scaffold_agent(project_ref: str, agent_name: str, model: str = "gpt-4.1", force: bool = False) -> Tuple[List[str], str, str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(agent_name)
    agents_dir = root / "agents"
    instr_dir = root / "instructions"
    agents_dir.mkdir(exist_ok=True)
    instr_dir.mkdir(exist_ok=True)
    agent_yml = agents_dir / f"{sanitized}.yml"
    instr_md = instr_dir / f"{sanitized}.md"
    created: List[str] = []
    if agent_yml.exists() and not force:
        raise FileExistsError(f"Agent YAML exists: {agent_yml}")
    if instr_md.exists() and not force:
        raise FileExistsError(f"Instructions exists: {instr_md}")
    def _titleize(s: str) -> str:
        parts = [p for p in s.split("_") if p]
        return " ".join(p.capitalize() for p in parts) or s
    display_label = _titleize(sanitized)
    agent_content = (
        f"name: {sanitized}\n"
        f"label: {display_label}\n"
        f"description: \"A helpful AI agent. Customize this description.\"\n"
        f"model: {model}\n"
        f"instructions_file: ../instructions/{sanitized}.md\n"
        f"welcome_text: \"Hello from {sanitized}.\"\n\n"
        "tools: []\n"
    )
    agent_yml.write_text(agent_content, encoding="utf-8")
    created.append(str(agent_yml))
    instr_md.write_text("You are a helpful assistant.", encoding="utf-8")
    created.append(str(instr_md))
    return created, str(root), sanitized


def scaffold_eval_quickstart(project_ref: str, force: bool = False) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    eval_dir = root / "evaluations"
    eval_dir.mkdir(exist_ok=True)
    created: List[str] = []
    evaluation_yml = eval_dir / "evaluation.yml"
    metrics_yml = eval_dir / "metrics.yml"
    judges_yml = eval_dir / "judges.yml"
    scenarios_yml = eval_dir / "scenarios.yml"
    measures_py = eval_dir / "measures.py"
    if (evaluation_yml.exists() or metrics_yml.exists() or judges_yml.exists() or scenarios_yml.exists() or measures_py.exists()) and not force:
        raise FileExistsError("Evaluations already exist; use --force to overwrite")
    evaluation_yml.write_text(
        "evaluation:\n  defaults:\n    runs_per_prompt: 1\n    max_turns: 6\n",
        encoding="utf-8",
    )
    created.append(str(evaluation_yml))
    metrics_yml.write_text(
        "metrics:\n  - name: unknown_tool_call_rate\n    label: Unknown Tool Call Rate\n    type: rate\n    from_measure: tool_hallucination\n    numerator: counts.unknown_tool_calls\n    denominator: counts.total_tool_calls\n",
        encoding="utf-8",
    )
    created.append(str(metrics_yml))
    judges_yml.write_text("judges: {}\n", encoding="utf-8")
    created.append(str(judges_yml))
    scenarios_yml.write_text(
        "scenarios:\n  - name: hello\n    prompt: Say hello\n",
        encoding="utf-8",
    )
    created.append(str(scenarios_yml))
    measures_py.write_text(
        "from omniagents.core.evaluation import evaluation_measure\n\n@evaluation_measure\ndef always_pass(ctx):\n    return {\"passed\": True}\n",
        encoding="utf-8",
    )
    created.append(str(measures_py))
    return created, str(root)


def scaffold_guardrail(project_ref: str, kind: str, name: str, make_async: bool = False, force: bool = False) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(name)
    guard_dir = root / "guardrails"
    guard_dir.mkdir(exist_ok=True)
    file_path = guard_dir / f"{sanitized}.py"
    if file_path.exists() and not force:
        raise FileExistsError(f"File exists: {file_path}")
    if kind == "input":
        if make_async:
            body = (
                "from omniagents import input_guardrail, GuardrailFunctionOutput\n\n"
                f"@input_guardrail()\n"
                f"async def {sanitized}(context, agent, input):\n"
                f"    return GuardrailFunctionOutput(output_info={{}}, tripwire_triggered=False)\n"
            )
        else:
            body = (
                "from omniagents import input_guardrail, GuardrailFunctionOutput\n\n"
                f"@input_guardrail()\n"
                f"def {sanitized}(context, agent, input):\n"
                f"    return GuardrailFunctionOutput(output_info={{}}, tripwire_triggered=False)\n"
            )
    else:
        if make_async:
            body = (
                "from omniagents import output_guardrail, GuardrailFunctionOutput\n\n"
                f"@output_guardrail()\n"
                f"async def {sanitized}(context, agent, agent_output):\n"
                f"    return GuardrailFunctionOutput(output_info={{}}, tripwire_triggered=False)\n"
            )
        else:
            body = (
                "from omniagents import output_guardrail, GuardrailFunctionOutput\n\n"
                f"@output_guardrail()\n"
                f"def {sanitized}(context, agent, agent_output):\n"
                f"    return GuardrailFunctionOutput(output_info={{}}, tripwire_triggered=False)\n"
            )
    file_path.write_text(body, encoding="utf-8")
    return [str(file_path)], str(root)


def scaffold_server_function(project_ref: str, name: str, make_async: bool = False, force: bool = False) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    sanitized = _sanitize_name(name)
    sf_dir = root / "server_functions"
    sf_dir.mkdir(exist_ok=True)
    file_path = sf_dir / f"{sanitized}.py"
    if file_path.exists() and not force:
        raise FileExistsError(f"File exists: {file_path}")
    if make_async:
        body = (
            "from omniagents import server_function\n\n"
            f"@server_function\n"
            f"async def {sanitized}(value: int = 1) -> dict:\n"
            f"    return {{'ok': True, 'value': value}}\n"
        )
    else:
        body = (
            "from omniagents import server_function\n\n"
            f"@server_function\n"
            f"def {sanitized}(value: int = 1) -> dict:\n"
            f"    return {{'ok': True, 'value': value}}\n"
        )
    file_path.write_text(body, encoding="utf-8")
    return [str(file_path)], str(root)


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
    created: List[str] = []

    judges_yml = eval_dir / "judges.yml"
    data = {"judges": {}}
    if judges_yml.exists():
        try:
            raw = yaml.safe_load(judges_yml.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                data = raw
                if not isinstance(data.get("judges"), dict):
                    data["judges"] = {}
        except Exception:
            if not force:
                raise
            data = {"judges": {}}
    judges = data.get("judges") or {}
    if sanitized in judges and not force:
        raise FileExistsError(f"Judge exists: {sanitized}")

    def _camel(s: str) -> str:
        parts = [p for p in s.split("_") if p]
        return "".join(p.capitalize() for p in parts)

    agent_name = _camel(sanitized)
    cat_placeholder = str(failure_category) if failure_category else "<<CRITERION_NAME>>"
    instructions = (
        "You are an expert evaluator assessing outputs from a <<ASSISTANT_SCOPE>> assistant.\n\n"
        "Your Task: Determine if the assistant satisfies the criterion: <<CRITERION_NAME>>.\n\n"
        "Evaluation Criterion: <<CRITERION_NAME>>\n"
        "Definition of Pass/Fail:\n"
        "Fail: <<FAIL_DEFINITION>>\n"
        "Pass: <<PASS_DEFINITION>>\n\n"
        "Output Format: Return ONLY a JSON object with:\n"
        "- reasoning: 1–2 sentence explanation for your decision.\n"
        "- answer: \"Pass\" or \"Fail\".\n\n"
        "Examples:\n"
        "Input 1:\n"
        "User/History: <<BRIEF_HISTORY_OR_CONTEXT_EX_1>>\n"
        "Assistant: \"<<ASSISTANT_REPLY_EX_1>>\"\n"
        "Evaluation 1:\n"
        "{\n  \"reasoning\": \"<<WHY_THIS_IS_FAIL_OR_PASS>>\",\n  \"answer\": \"Fail\"\n}\n\n"
        "Input 2:\n"
        "User/History: <<BRIEF_HISTORY_OR_CONTEXT_EX_2>>\n"
        "Assistant: \"<<ASSISTANT_REPLY_EX_2>>\"\n"
        "Evaluation 2:\n"
        "{\n  \"reasoning\": \"<<WHY_THIS_IS_FAIL_OR_PASS>>\",\n  \"answer\": \"Pass\"\n}\n\n"
        "Only return the JSON object; no extra text.\n"
    )
    template = (
        f"Now, evaluate the following against the criterion: {cat_placeholder}.\n"
        "Return JSON {reasoning:string, answer:\"Pass\"|\"Fail\"}.\n\n"
        "Assistant Message:\n---\n{{ text }}\n---\n\n"
        "Conversation History (JSON):\n---\n{{ history_json }}\n---\n\n"
        "Your JSON Evaluation:\n"
    )
    jcfg = {
        "agent": {
            "name": agent_name,
            "model": model,
            "output_type": "dict",
            "output_type_strict": False,
            "output_schema": {
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string"},
                    "answer": {"type": "string", "enum": ["Pass", "Fail"]},
                },
                "required": ["reasoning", "answer"],
                "additionalProperties": False,
            },
            "instructions": instructions,
        },
        "input": {
            "source": source,
            "template": template,
        },
    }
    judges[sanitized] = jcfg
    data["judges"] = judges
    judges_yml.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    created.append(str(judges_yml))

    if add_measure:
        measures_py = eval_dir / "measures.py"
        fn_name = f"{sanitized}_agent"
        lines = []
        lines.append("from omniagents.core.evaluation import evaluation_measure")
        lines.append("")
        lines.append("@evaluation_measure")
        lines.append(f"def {fn_name}(ctx):")
        lines.append("    meta = getattr(ctx, 'metadata', {}) or {}")
        lines.append(f"    judge = meta.get('{sanitized}')")
        lines.append("    if not isinstance(judge, dict):")
        lines.append("        return {'passed': False, 'reason': 'no_judge_result'}")
        lines.append("    ans = str(judge.get('answer', '')).strip().lower()")
        lines.append("    passed = (ans == 'pass')")
        lines.append("    return {'passed': passed, 'judge': judge}")
        block = "\n".join(lines) + "\n"
        if measures_py.exists():
            with measures_py.open("a", encoding="utf-8") as f:
                f.write("\n" + block)
        else:
            measures_py.write_text(block, encoding="utf-8")
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
        evaluation_yml.write_text(yaml.safe_dump(base, sort_keys=False, allow_unicode=True), encoding="utf-8")
        created.append(str(evaluation_yml))

    return created, str(root)
