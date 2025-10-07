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

    # New per-entity agent layout
    agent_dir = agents_dir / sanitized
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_yml = agent_dir / "agent.yml"
    agent_instr = agent_dir / "instructions.md"
    if not agent_instr.exists() or force:
        agent_instr.write_text(
            (
                "<1–2 sentence blurb establishing identity and high-level situation to prime the LLM>\n\n"
                "## Persona\n"
                "<the role, expertise, and voice to adopt>\n\n"
                "## Goal\n"
                "<the concrete objective and success criteria>\n\n"
                "## Starting Context\n"
                "<initial inputs, available tools, and constraints>\n\n"
                "## Guidance\n"
                "<step-by-step rules, tool order, do/don’ts, output format>\n\n"
                "## Examples\n"
                "<few-shot input→output pairs to show format and edge cases>\n"
            ),
            encoding="utf-8",
        )
        created.append(str(agent_instr))
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
            f"instructions_file: instructions.md\n"
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
    def _titleize(s: str) -> str:
        parts = [p for p in s.split("_") if p]
        return " ".join(p.capitalize() for p in parts) or s
    display_label = _titleize(sanitized)
    agent_content = (
        f"name: {sanitized}\n"
        f"label: {display_label}\n"
        f"description: \"A helpful AI agent. Customize this description.\"\n"
        f"model: {model}\n"
        f"instructions_file: instructions.md\n"
        f"welcome_text: \"Hello from {sanitized}.\"\n\n"
        "tools: []\n"
    )
    agent_yml.write_text(agent_content, encoding="utf-8")
    created.append(str(agent_yml))
    instr_md.write_text(
        (
            "<1–2 sentence blurb establishing identity and high-level situation to prime the LLM>\n\n"
            "## Persona\n"
            "<the role, expertise, and voice to adopt>\n\n"
            "## Goal\n"
            "<the concrete objective and success criteria>\n\n"
            "## Starting Context\n"
            "<initial inputs, available tools, and constraints>\n\n"
            "## Guidance\n"
            "<step-by-step rules, tool order, do/don’ts, output format>\n\n"
            "## Examples\n"
            "<few-shot input→output pairs to show format and edge cases>\n"
        ),
        encoding="utf-8",
    )
    created.append(str(instr_md))
    return created, str(root), sanitized


def scaffold_eval_quickstart(project_ref: str, force: bool = False) -> Tuple[List[str], str]:
    root = _resolve_project_root(project_ref)
    eval_dir = root / "evaluations"
    eval_dir.mkdir(exist_ok=True)
    created: List[str] = []
    evaluation_yml = eval_dir / "evaluation.yml"
    metrics_yml = eval_dir / "metrics.yml"
    scenarios_yml = eval_dir / "scenarios.yml"
    measures_py = eval_dir / "measures.py"
    if (evaluation_yml.exists() or metrics_yml.exists() or scenarios_yml.exists() or measures_py.exists()) and not force:
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
    # judges are now per-judge under evaluations/judges/<key>/
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
    judges_root = eval_dir / "judges" / sanitized
    judges_root.mkdir(parents=True, exist_ok=True)
    created: List[str] = []

    def _camel(s: str) -> str:
        parts = [p for p in s.split("_") if p]
        return "".join(p.capitalize() for p in parts)
    def _titleize(s: str) -> str:
        parts = [p for p in s.split("_") if p]
        return " ".join(p.capitalize() for p in parts) or s

    agent_name = _camel(sanitized)
    agent_label = _titleize(sanitized)

    judge_yml = judges_root / "judge.yml"
    if judge_yml.exists() and not force:
        raise FileExistsError(f"Judge exists: {judge_yml}")

    judge_instructions_md = judges_root / "instructions.md"
    judge_template_md = judges_root / "template.md"

    judge_cfg = {
        "judges": {
            sanitized: {
                "agent": {
                    "name": agent_name,
                    "label": agent_label,
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
                    "instructions_file": f"../evaluations/judges/{sanitized}/instructions.md",
                },
                "input": {
                    "source": source,
                    "template_file": f"../evaluations/judges/{sanitized}/template.md",
                },
            }
        }
    }

    judge_yml.write_text(yaml.safe_dump(judge_cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    created.append(str(judge_yml))

    if not judge_instructions_md.exists() or force:
        judge_instructions_md.write_text(
            (
                "AI Agent Information\n"
                "- Name: {{ agent_label or agent_name }}\n"
                "{% if agent_description -%}\n- Description: {{ agent_description }}\n{%- endif %}\n\n"
                "Role\n"
                "- You are an evaluation judge for the category: {{ category }}.\n\n"
                "Goal\n"
                "- Decide whether the assistant meets the criterion defined below.\n\n"
                "Input\n"
                "- You will receive a conversation trace between a user and the AI Agent described above.\n\n"
                "Output Schema (Do Not Change)\n"
                "- Return ONLY JSON with keys:\n"
                "  - reasoning: string\n"
                "  - answer: \"Pass\" or \"Fail\"\n"
                "- Example:\n"
                "  {\"reasoning\": \"...\", \"answer\": \"Pass\"}\n\n"
                "Decision Policy\n"
                "- Positive (label Fail): [FILL: WHAT COUNTS AS A VIOLATION]\n"
                "- Negative (label Pass): [FILL: WHAT COUNTS AS ACCEPTABLE BEHAVIOR]\n"
                "- Use only the provided input and any structured fields (e.g., history_json). Do not infer outside context.\n\n"
                "Rules\n"
                "1) Core decision rules:\n"
                "   - [FILL: RULE 1]\n"
                "   - [FILL: RULE 2]\n"
                "   - [FILL: RULE 3]\n"
                "2) Edge cases and clarifications:\n"
                "   - [FILL: EDGE CASE 1]\n"
                "   - [FILL: EDGE CASE 2]\n"
                "3) Tie-breakers (when ambiguous):\n"
                "   - [FILL: TIEBREAKER 1]\n"
                "   - [FILL: TIEBREAKER 2]\n"
                "4) Evidence requirements:\n"
                "   - Require clear evidence before deciding Fail; otherwise prefer Pass.\n"
                "   - Do not reward/penalize style unless it affects the criterion.\n\n"
                "Common Pitfalls\n"
                "- [FILL: PITFALL 1]\n"
                "- [FILL: PITFALL 2]\n"
                "- [FILL: PITFALL 3]\n\n"
                "Examples\n"
                "- Example A (Pass — Meets criterion)\n"
                "  - Case: [FILL: CLEAR PASS CASE]\n"
                "  - Why: [FILL: SPECIFIC REASON FOR PASS]\n"
                "  - Output:\n"
                "  {\"reasoning\": \"[FILL: PASS REASONING]\", \"answer\": \"Pass\"}\n\n"
                "- Example B (Fail — Violates criterion)\n"
                "  - Case: [FILL: CLEAR FAIL CASE]\n"
                "  - Why: [FILL: SPECIFIC REASON FOR FAIL]\n"
                "  - Output:\n"
                "  {\"reasoning\": \"[FILL: FAIL REASONING]\", \"answer\": \"Fail\"}\n\n"
                "- Example C (Pass — Ambiguous/insufficient evidence)\n"
                "  - Case: [FILL: AMBIGUOUS/INSUFFICIENT EVIDENCE CASE]\n"
                "  - Why: [FILL: WHY AMBIGUITY ⇒ PASS]\n"
                "  - Output:\n"
                "  {\"reasoning\": \"[FILL: AMBIGUITY REASONING]\", \"answer\": \"Pass\"}\n\n"
                "- Example D (Pass — Valid clarification)\n"
                "  - Case: [FILL: CLARIFICATION CASE THAT REMAINS ON-CRITERION]\n"
                "  - Why: [FILL: WHY CLARIFICATION ⇒ PASS]\n"
                "  - Output:\n"
                "  {\"reasoning\": \"[FILL: CLARIFICATION REASONING]\", \"answer\": \"Pass\"}\n\n"
                "Return ONLY the JSON object per schema—no extra text.\n"
            ),
            encoding="utf-8",
        )
        created.append(str(judge_instructions_md))

    if not judge_template_md.exists() or force:
        judge_template_md.write_text(
            (
                "Decide if the assistant satisfies the evaluation criterion for this case.\n"
                "Return JSON {reasoning:string, answer:\"Pass\"|\"Fail\"}.\n\n"
                "Conversation history (JSON):\n---\n{{ history_json }}\n---\n\n"
                "Assistant message:\n---\n{{ text }}\n---\n"
            ),
            encoding="utf-8",
        )
        created.append(str(judge_template_md))

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

    def _camel(s: str) -> str:
        parts = [p for p in s.split("_") if p]
        return "".join(p.capitalize() for p in parts)

    agent_name = _camel(sanitized)

    optimizer_yml = opt_root / "optimizer.yml"
    if optimizer_yml.exists() and not force:
        raise FileExistsError(f"Optimizer exists: {optimizer_yml}")

    optimizer_instructions_md = opt_root / "instructions.md"
    rewrite_instr_md = opt_root / "rewrite_instructions.md"
    rewrite_tmpl_md = opt_root / "rewrite_template.md"

    opt_cfg = {
        "optimizers": {
            sanitized: {
                "agent": {
                    "name": agent_name,
                    "model": model,
                    "instructions_file": f"../evaluations/optimizers/{sanitized}/instructions.md",
                },
                "proposals": {
                    "instructions_template_file": f"../evaluations/optimizers/{sanitized}/rewrite_instructions.md",
                    "template_template_file": f"../evaluations/optimizers/{sanitized}/rewrite_template.md",
                },
            }
        }
    }

    optimizer_yml.write_text(yaml.safe_dump(opt_cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    created.append(str(optimizer_yml))

    if not optimizer_instructions_md.exists() or force:
        if str(kind).lower() == "judge":
            body = (
                "You are a prompt optimizer for judge components.\n\n"
                "Goal: Propose improved text strictly within a fenced code block.\n"
                "Focus on judge instructions and templates; do not add commentary."
            )
        else:
            body = (
                "You are a prompt optimizer.\n\n"
                "Goal: Propose improved text strictly within a fenced code block.\n"
                "Do not add commentary outside the fence."
            )
        optimizer_instructions_md.write_text(body, encoding="utf-8")
        created.append(str(optimizer_instructions_md))

    if not rewrite_instr_md.exists() or force:
        if str(kind).lower() == "judge":
            body = (
                "You are optimizing the LLM-as-Judge’s instructions to improve classification accuracy across scenarios.\n\n"
                "# Judge Information\n"
                "- Role: judge\n"
                "- Name: {{ judge_label or judge_name }}\n"
                "{% if judge_description -%}\n- Description: {{ judge_description }}\n{%- endif %}\n"
                "{% if category -%}\n- Target Category: {{ category }}\n{%- endif %}\n"
                "- Input: a conversation trace between a user and an AI Agent\n\n"
                "# AI Agent Information\n"
                "- Name: {{ agent_label or agent_name }}\n"
                "{% if agent_description -%}\n- Description: {{ agent_description }}\n{%- endif %}\n\n"
                "## Current Instructions\n```\n{{ current_instruction_doc }}\n```\n\n"
                "## Available Tools (if any)\n{{ tools_schema_md }}\n\n"
                "# Goal\n"
                "Write improved, generalizable judge instructions that maximize agreement with labels without overfitting.\n\n"
                "# Context\n"
                "The reflective examples below include task inputs, the judge’s outputs, and feedback indicating where predictions matched or mismatched labels. Use them to refine decision rules and edge-case handling without memorizing examples.\n\n"
                "```\n{{ dataset_md }}\n```\n\n"
                "# Output Schema (Do Not Change)\n"
                "- The judge must return ONLY JSON with keys: reasoning (string), answer (\"Pass\" or \"Fail\").\n"
                "- Do not add, rename, or remove keys; do not change allowed values.\n"
                "- Example:\n"
                "```json\n{\n  \"reasoning\": \"...\",\n  \"answer\": \"Pass\"\n}\n```\n\n"
                "# Guidance\n"
                "- Do: clarify decision rules; include edge-case handling and tie-breakers.\n"
                "- Don’t: rely on example-specific artifacts; avoid leaking labels or overfitting.\n"
                "- Tools: use tools for verification only; do not bias outcomes.\n"
                "- Output format: return ONLY the new instructions in a single fenced code block. No commentary.\n"
            )
        else:
            body = (
                "You are optimizing the LLM agent’s instructions to improve performance across scenarios.\n\n"
                "# LLM Agent Information\n"
                "- Role: {{ target_role or 'assistant' }}\n"
                "- Name: {{ agent_label or agent_name or judge_label or judge_name }}\n"
                "{% if agent_description or judge_description -%}\n- Description: {{ agent_description or judge_description }}\n{%- endif %}\n\n"
                "## Current Instructions\n```\n{{ current_instruction_doc }}\n```\n\n"
                "## Available Tools (if any)\n{{ tools_schema_md }}\n\n"
                "# Goal\n"
                "Write improved, generalizable instructions that maximize correct behavior across the dataset without overfitting.\n\n"
                "# Context\n"
                "The reflective examples below include task inputs, the LLM agent’s responses, and feedback highlighting successes and failures. Use them to identify generalizable decision rules and edge cases. Do not copy phrasing verbatim or overfit to specific example IDs.\n\n"
                "```\n{{ dataset_md }}\n```\n\n"
                "# Guidance\n"
                "- Do: include domain constraints and decision rules; be explicit about edge cases.\n"
                "- Don’t: overfit to specific IDs; avoid brittle patterns; don’t require unavailable context.\n"
                "- Tools: use tools when they improve correctness; follow parameter schemas.\n"
                "- Output format: return ONLY the new instructions in a single fenced code block. No commentary.\n"
            )
        rewrite_instr_md.write_text(body, encoding="utf-8")
        created.append(str(rewrite_instr_md))

    if not rewrite_tmpl_md.exists() or force:
        body = (
            "You are optimizing the judge input template used to render evaluation inputs.\n\n"
            "# Judge Information\n"
            "- Role: judge\n"
            "- Name: {{ judge_label or judge_name }}\n"
            "{% if judge_description -%}\n- Description: {{ judge_description }}\n{%- endif %}\n"
            "{% if category -%}\n- Target Category: {{ category }}\n{%- endif %}\n"
            "- Input: a conversation trace between a user and an AI Agent\n\n"
            "# AI Agent Information\n"
            "- Name: {{ agent_label or agent_name }}\n"
            "{% if agent_description -%}\n- Description: {{ agent_description }}\n{%- endif %}\n\n"
            "## Current Template\n```\n{{ current_template }}\n```\n\n"
            "## Available Tools (if any)\n{{ tools_schema_md }}\n\n"
            "# Goal\n"
            "Write an improved, generalizable input template that presents the right evidence and constraints for correct classification. Preserve and correctly place required placeholders (e.g., {{ text }}, {{ history_json }}, etc.).\n\n"
            "# Context\n"
            "The reflective examples below include input text (and history where applicable), the judge’s outputs, and feedback indicating where predictions matched or mismatched labels. Use them to adjust what evidence and instructions the template surfaces without overfitting to specific instances.\n\n"
            "```\n{{ dataset_md }}\n```\n\n"
            "# Guidance\n"
            "- Preserve required placeholders and Jinja syntax.\n"
            "- Do not leak or hint at ground-truth labels.\n"
            "- Be concise but complete; surface the artifacts the judge needs (e.g., history_json when relevant).\n"
            "- Output format: return ONLY the new template in a single fenced code block. No commentary.\n"
        )
        rewrite_tmpl_md.write_text(body, encoding="utf-8")
        created.append(str(rewrite_tmpl_md))

    return created, str(root)
