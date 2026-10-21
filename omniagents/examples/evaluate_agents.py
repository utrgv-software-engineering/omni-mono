from __future__ import annotations

import argparse
import asyncio
import json
import glob as _glob
from pathlib import Path
from typing import List, Dict, Any

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.factory import create_unified_agent
from omniagents.core.session.history_db import load_history
from omniagents.core.evaluation import build_eval_context, run_measures


async def _run_agent_once(
    agent_yaml: str, prompt: str, measures: List[str]
) -> Dict[str, Any]:
    spec = load_agent_spec_from_yaml(agent_yaml)
    spec.use_safe_agent = False
    agent = await create_unified_agent(spec)
    msgs = [{"role": "user", "content": prompt}]
    result = agent.run_streamed(msgs)
    async for _ in result.stream_events():
        pass
    session_id = getattr(agent, "_session_id", None)
    history = (
        load_history(
            session_id,
            project_slug=spec.project_slug,
            agent_slug=spec.agent_slug,
        )
        if session_id
        else []
    )
    ctx = build_eval_context(history=history, tools=spec.available_tools)
    out = run_measures(ctx, measures=measures)
    return {
        "agent_yaml": agent_yaml,
        "session_id": session_id,
        "prompt": prompt,
        "measures": out,
        "history_len": len(history),
    }


async def _main_async(args) -> Dict[str, Any]:
    paths: List[str] = []
    for pat in args.agents:
        paths.extend(_glob.glob(pat))
    paths = [str(Path(p).resolve()) for p in paths]
    paths = [p for p in paths if Path(p).is_file()]
    measures = [m.strip() for m in args.measures.split(",") if m.strip()]
    prompts = args.prompt if isinstance(args.prompt, list) else [args.prompt]
    results: List[Dict[str, Any]] = []
    for agent_yaml in paths:
        for prompt in prompts:
            r = await _run_agent_once(agent_yaml, prompt, measures)
            results.append(r)
    summary = {
        "runs": results,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate multiple agents with measures"
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        default=["examples/*/agent.yml"],
        help="YAML paths or globs",
    )
    parser.add_argument(
        "--prompt",
        action="append",
        default=["Weather in Paris"],
        help="Prompt(s) to run; can repeat",
    )
    parser.add_argument(
        "--measures", default="tool_hallucination", help="Comma-separated measure names"
    )
    parser.add_argument("--output", default="", help="Write JSON to file if provided")
    args = parser.parse_args()

    data = asyncio.run(_main_async(args))
    text = json.dumps(data, indent=2)
    if args.output:
        Path(args.output).write_text(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
