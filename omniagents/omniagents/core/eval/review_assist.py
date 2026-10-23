"""Help reviewing evaluation answers: the person notices, this organizes.

Error analysis is a person reading answers, marking Pass or Fail and writing
what went wrong. Three kinds of help, none of which labels anything until a
person accepts it:

- :func:`draw_sample`: which unreviewed answers to read next, a mix of
  representatives of clusters of similar answers (so the sample covers the
  kinds of answers there are) and random picks (so it covers what the
  clustering missed).
- :func:`propose_failure_modes`: people's free-text notes grouped into
  failure modes, matched to the project's categories where they fit and
  proposed as new ones where none does.
- :func:`suggest_labels`: answers that may show a failure mode, reviewed or
  not, found by reading every answer against the mode's definition and the
  answers people already labelled with it. It favours recall: dismissing a
  wrong suggestion is quick, missing a real instance is not.

Their state is kept in ``runs/review-assist/`` (local, like runs): the
answers are the saved runs' conversations, and what is accepted is written
as reviews (:mod:`reviews`).
"""

from __future__ import annotations

import json
import math
import random
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from omniagents.core.eval.layout import EvalLayout
from omniagents.core.eval.results import list_runs
from omniagents.core.eval.reviews import list_reviews

DEFAULT_MODEL = "gpt-4.1-2025-04-14"
SAMPLE_FILE = "sample.json"
PROPOSALS_FILE = "proposals.json"
SUGGESTIONS_FILE = "suggestions.json"
#: Share of a sample drawn as cluster representatives; the rest is random.
REPRESENTATIVE_SHARE = 0.65
REPLY_CHARS = 2000
SCAN_BATCH = 8

Ask = Callable[[str, str], Any]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read(layout: EvalLayout, name: str, default: Any) -> Any:
    path = layout.review_assist_dir / name
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(layout: EvalLayout, name: str, data: Any) -> None:
    layout.review_assist_dir.mkdir(parents=True, exist_ok=True)
    (layout.review_assist_dir / name).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def state(layout: EvalLayout) -> Dict[str, Any]:
    """The current sample, proposals and suggestions."""
    return {
        "sample": _read(layout, SAMPLE_FILE, None),
        "proposals": _read(layout, PROPOSALS_FILE, []),
        "suggestions": _read(layout, SUGGESTIONS_FILE, []),
    }


# -- the answers ------------------------------------------------------------


def _tool_calls(history: Sequence[Any]) -> int:
    return sum(
        1
        for item in history or []
        if isinstance(item, dict)
        and (item.get("type") == "function_call" or item.get("role") == "tool")
    )


def answers(layout: EvalLayout) -> List[Dict[str, Any]]:
    """Every evaluation answer in the saved runs and baselines, once each."""
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for entry in list_runs(layout):
        path = Path(entry["path"])
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for record in data.get("runs") or []:
            group = record.get("trace_group_id")
            if not group or group in seen:
                continue
            seen.add(group)
            scenario = record.get("scenario_config") or {}
            history = record.get("history") or []
            out.append(
                {
                    "group_id": group,
                    "run": entry.get("id"),
                    "scenario": str(scenario.get("id") or scenario.get("name") or ""),
                    "prompt": scenario.get("prompt")
                    or (scenario.get("prompts") or [None])[0]
                    or scenario.get("goal"),
                    "dimensions": dict(scenario.get("dimensions") or {}),
                    "reply": record.get("final_assistant_text") or "",
                    "tool_calls": _tool_calls(history),
                    "turns": sum(
                        1
                        for item in history
                        if isinstance(item, dict) and item.get("role") == "user"
                    ),
                    "passed": bool(record.get("passed")),
                }
            )
    return out


# -- what to review next -------------------------------------------------------


def _features(items: Sequence[Dict[str, Any]]) -> List[List[float]]:
    """Each answer as numbers: its size and shape, whether it passed, and its
    scenario's dimension values (one column per value), standardized."""
    values = sorted(
        {(k, str(v)) for item in items for k, v in item["dimensions"].items()}
    )
    rows = [
        [
            math.log1p(len(item["reply"])),
            float(item["tool_calls"]),
            float(item["turns"]),
            1.0 if item["passed"] else 0.0,
            *(1.0 if str(item["dimensions"].get(k)) == v else 0.0 for k, v in values),
        ]
        for item in items
    ]
    if not rows:
        return rows
    columns = list(zip(*rows))
    scaled = []
    for column in columns:
        mean = sum(column) / len(column)
        spread = math.sqrt(sum((x - mean) ** 2 for x in column) / len(column)) or 1.0
        scaled.append([(x - mean) / spread for x in column])
    return [list(row) for row in zip(*scaled)]


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _kmeans(points: List[List[float]], k: int, rng: random.Random) -> List[int]:
    """Cluster ids for ``points`` (k-means with k-means++ starts)."""
    centers = [points[rng.randrange(len(points))]]
    while len(centers) < k:
        weights = [min(_distance(p, c) for c in centers) for p in points]
        total = sum(weights)
        if total <= 0:
            break
        pick, acc = rng.random() * total, 0.0
        for point, weight in zip(points, weights):
            acc += weight
            if acc >= pick:
                centers.append(point)
                break
    labels = [0] * len(points)
    for _ in range(50):
        new = [
            min(range(len(centers)), key=lambda c: _distance(p, centers[c]))
            for p in points
        ]
        if new == labels and _ > 0:
            break
        labels = new
        for c in range(len(centers)):
            members = [p for p, label in zip(points, labels) if label == c]
            if members:
                centers[c] = [sum(col) / len(members) for col in zip(*members)]
    return labels


def draw_sample(layout: EvalLayout, *, size: int = 20, seed: int = 0) -> Dict[str, Any]:
    """Choose ``size`` unreviewed answers to read next: about two thirds
    representatives of clusters of similar answers, a third at random."""
    reviewed = {r["group_id"] for r in list_reviews(layout)}
    pool = [a for a in answers(layout) if a["group_id"] not in reviewed]
    rng = random.Random(seed)
    items: List[Dict[str, Any]] = []
    if pool:
        size = min(size, len(pool))
        points = _features(pool)
        k = max(1, min(8, len(pool) // 4))
        labels = _kmeans(points, k, rng)
        centers = {}
        for c in set(labels):
            members = [p for p, label in zip(points, labels) if label == c]
            centers[c] = [sum(col) / len(members) for col in zip(*members)]
        queues = {
            c: sorted(
                (i for i, label in enumerate(labels) if label == c),
                key=lambda i: _distance(points[i], centers[c]),
            )
            for c in centers
        }
        chosen: set[int] = set()
        target = round(size * REPRESENTATIVE_SHARE)
        while len(chosen) < target and any(queues.values()):
            for c in sorted(queues):
                if queues[c] and len(chosen) < target:
                    i = queues[c].pop(0)
                    chosen.add(i)
                    items.append(
                        {
                            "group_id": pool[i]["group_id"],
                            "reason": f"represents cluster {c + 1} of {len(centers)}",
                        }
                    )
        rest = [i for i in range(len(pool)) if i not in chosen]
        rng.shuffle(rest)
        for i in rest[: size - len(items)]:
            items.append({"group_id": pool[i]["group_id"], "reason": "random pick"})
    sample = {"drawn_at": _now(), "seed": seed, "unreviewed": len(pool), "items": items}
    _write(layout, SAMPLE_FILE, sample)
    return sample


# -- asking a model -----------------------------------------------------------


def model_asker(model: str) -> Ask:
    """``ask(instructions, prompt) -> dict``: one call to ``model`` that
    returns a JSON object."""

    async def ask(instructions: str, prompt: str) -> Dict[str, Any]:
        from agents import Runner

        from omniagents.core.agents.builder import _default_build_agent
        from omniagents.core.agents.specs import AgentSpec

        spec = AgentSpec(
            name="ReviewAssistant",
            welcome_text="",
            get_agent_instructions=lambda: instructions,
            build_agent=None,
            available_tools=[],
            model_name=model,
            output_type=dict,
            output_type_strict=False,
            project_slug=None,
            agent_slug="review_assistant",
        )
        agent = await _default_build_agent({}, None, spec, None)
        result = await Runner.run(agent, prompt, max_turns=1)
        output = result.final_output
        if isinstance(output, str):
            output = json.loads(output)
        return output if isinstance(output, dict) else {}

    return ask


# -- failure modes from notes -------------------------------------------------

PROPOSE_INSTRUCTIONS = """\
You organize a reviewer's notes on an AI agent's answers into failure modes.

Each note says what a reviewer saw wrong in one answer. Group the notes by
the failure they describe:
- Where a note fits one of the existing categories, use it (give its id).
- Propose a new mode only for failures no existing category covers.
- Name a new mode for the specific, observable failure in this application
  ("Asks for a location it was given", not "Hallucination"), and describe it
  in one sentence a second reviewer could apply.
- A note may show several failures; list it under each.
- Leave out notes that describe no failure.

Return JSON: {"modes": [{"existing_category": "<id>" or null, "name": "...",
"description": "...", "note_ids": ["..."]}]}
"""


async def propose_failure_modes(
    layout: EvalLayout,
    notes: Sequence[Dict[str, Any]],
    categories: Sequence[Dict[str, Any]],
    ask: Ask,
) -> List[Dict[str, Any]]:
    """Group ``notes`` (``{id, verdict, notes}``) into failure modes, matched
    to ``categories`` (``{id, name, description}``) where they fit. Replaces
    the pending proposals."""
    usable = [n for n in notes if str(n.get("notes") or "").strip()]
    if not usable:
        raise ValueError("There are no review notes to organize yet")
    known = {c["id"] for c in categories}
    prompt = json.dumps(
        {
            "existing_categories": [
                {"id": c["id"], "name": c["name"], "description": c.get("description")}
                for c in categories
            ],
            "notes": [
                {"id": n["id"], "verdict": n.get("verdict"), "note": n["notes"]}
                for n in usable
            ],
        },
        ensure_ascii=False,
    )
    output = await ask(PROPOSE_INSTRUCTIONS, prompt)
    by_id = {n["id"]: n for n in usable}
    proposals = []
    for mode in output.get("modes") or []:
        if not isinstance(mode, dict):
            continue
        ids = [i for i in mode.get("note_ids") or [] if i in by_id]
        if not ids:
            continue
        existing = mode.get("existing_category")
        proposals.append(
            {
                "id": secrets.token_hex(4),
                "existing_category": existing if existing in known else None,
                "name": str(mode.get("name") or "").strip() or "Unnamed failure",
                "description": str(mode.get("description") or "").strip() or None,
                "review_ids": ids,
                "notes": [by_id[i]["notes"] for i in ids],
                "proposed_at": _now(),
            }
        )
    _write(layout, PROPOSALS_FILE, proposals)
    return proposals


def take_proposal(layout: EvalLayout, proposal_id: str) -> Dict[str, Any]:
    """Remove a pending proposal and return it (accepted or dismissed)."""
    proposals = _read(layout, PROPOSALS_FILE, [])
    found = next((p for p in proposals if p["id"] == proposal_id), None)
    if found is None:
        raise KeyError(f"No proposal {proposal_id!r}")
    _write(layout, PROPOSALS_FILE, [p for p in proposals if p["id"] != proposal_id])
    return found


# -- labels suggested for a failure mode ----------------------------------------

SUGGEST_INSTRUCTIONS = """\
You check an AI agent's answers for one failure mode.

For each answer, decide whether it shows the failure described. Favour
recall: flag an answer when it plausibly shows the failure, since a person
reviews every flag. For each flagged answer, quote the specific text that
shows it and say why in one sentence.

Return JSON: {"matches": [{"id": "...", "quote": "...", "reason": "..."}]}
"""


async def suggest_labels(
    layout: EvalLayout,
    category: Dict[str, Any],
    ask: Ask,
    *,
    batch: int = SCAN_BATCH,
) -> List[Dict[str, Any]]:
    """Read every answer not yet labelled with ``category`` against its
    definition and the answers people labelled with it, and add a pending
    suggestion for each that may show it. A suggestion once dismissed is not
    made again."""
    reviews = {r["group_id"]: r for r in list_reviews(layout)}
    pool = answers(layout)
    labelled = [
        a
        for a in pool
        if category["id"]
        in (reviews.get(a["group_id"], {}).get("failure_categories") or [])
    ]
    existing = _read(layout, SUGGESTIONS_FILE, [])
    decided = {
        (s["group_id"], s["category"]) for s in existing if s["status"] != "pending"
    }
    pending = {
        (s["group_id"], s["category"]) for s in existing if s["status"] == "pending"
    }
    candidates = [
        a
        for a in pool
        if a not in labelled
        and (a["group_id"], category["id"]) not in decided
        and (a["group_id"], category["id"]) not in pending
    ]
    examples = [
        {
            "reply": a["reply"][:REPLY_CHARS],
            "reviewer_note": reviews[a["group_id"]].get("notes"),
        }
        for a in labelled[:3]
    ]
    added: List[Dict[str, Any]] = []
    for start in range(0, len(candidates), batch):
        chunk = candidates[start : start + batch]
        prompt = json.dumps(
            {
                "failure_mode": {
                    "name": category["name"],
                    "description": category.get("description"),
                },
                "labelled_examples": examples,
                "answers": [
                    {
                        "id": a["group_id"],
                        "prompt": a["prompt"],
                        "reply": a["reply"][:REPLY_CHARS],
                    }
                    for a in chunk
                ],
            },
            ensure_ascii=False,
        )
        output = await ask(SUGGEST_INSTRUCTIONS, prompt)
        ids = {a["group_id"] for a in chunk}
        for match in output.get("matches") or []:
            if not isinstance(match, dict) or match.get("id") not in ids:
                continue
            added.append(
                {
                    "id": secrets.token_hex(4),
                    "group_id": match["id"],
                    "category": category["id"],
                    "quote": str(match.get("quote") or "").strip() or None,
                    "reason": str(match.get("reason") or "").strip() or None,
                    "reviewed": match["id"] in reviews,
                    "status": "pending",
                    "suggested_at": _now(),
                }
            )
    _write(layout, SUGGESTIONS_FILE, existing + added)
    return added


def decide_suggestions(
    layout: EvalLayout, ids: Sequence[str], status: str
) -> List[Dict[str, Any]]:
    """Mark pending suggestions ``accepted`` or ``dismissed``; returns them."""
    if status not in ("accepted", "dismissed"):
        raise ValueError("A suggestion is accepted or dismissed")
    wanted = set(ids)
    suggestions = _read(layout, SUGGESTIONS_FILE, [])
    decided = []
    for suggestion in suggestions:
        if suggestion["id"] in wanted and suggestion["status"] == "pending":
            suggestion["status"] = status
            suggestion["decided_at"] = _now()
            decided.append(suggestion)
    _write(layout, SUGGESTIONS_FILE, suggestions)
    return decided


def assistant_model(settings: Dict[str, Any]) -> str:
    """The model the assistant asks: ``review_assistant.model`` in
    ``evaluation.yml``, else a pinned default."""
    configured = (settings.get("review_assistant") or {}).get("model")
    return str(configured) if configured else DEFAULT_MODEL
