"""What governance produces for review: system cards, the AI Inventory,
scheduled reviews, the quarterly report, and the evidence export.

Health Affairs governs its own AI risk; this is how it shows its work. Each is
generated from records Workplace already keeps (settings, decisions, the audit
log, scheduled-review completions), never written by hand, and none contains
conversation content or files: everything here can be handed over.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

DAY = 86400.0

#: Scheduled reviews: kind, title, cadence in days, who performs it, per factory.
REVIEWS = [
    ("restore_drill", "Restore drill", 91, "administrator", False),
    ("kill_switch_drill", "Kill-switch drill", 91, "administrator", False),
    ("access_review", "Access review", 365, "governance owner", False),
    ("risk_review", "Risk review", 365, "governance owner", True),
    ("inventory_update", "AI Inventory update", 730, "governance owner", False),
    (
        "heightened_risk_reassessment",
        "Heightened-risk reassessment",
        182,
        "governance owner",
        True,
    ),
]
REVIEW_KIND = "governance.review"
MATERIAL_KINDS = {"labels", "allowed_services", "model", "pull_request"}
#: Audit events reports call out.
KILL_SWITCH = {
    "factory.stopped",
    "factory.resumed",
    "installation.stopped",
    "installation.resumed",
}


def stamp(at: Optional[float]) -> str:
    if not at:
        return "—"
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# -- system cards ------------------------------------------------------------


def project_settings(project_dir: Optional[Path]) -> dict:
    if not project_dir or not (Path(project_dir) / "project.yml").is_file():
        return {}
    return yaml.safe_load((Path(project_dir) / "project.yml").read_text()) or {}


def system_card(w: Any, name: str, project_dir: Optional[Path]) -> dict:
    """A factory as the AI Inventory needs it, from what Workplace holds."""
    g = w.governance
    profile = g.profile()
    project = project_settings(project_dir)
    decisions = g.decisions.all()

    def last(kind: str, settled=("decided", "applied")) -> Optional[dict]:
        found = [d for d in decisions if d["kind"] == kind and d["status"] in settled]
        return found[-1] if found else None

    labels = last("labels")
    material = [
        d
        for d in decisions
        if d["kind"] in MATERIAL_KINDS and d["status"] in ("decided", "applied")
    ]
    models: List[str] = []
    try:
        from .factory_models import FactoryModels

        config = (
            (w.factory_config.load().get("factory") or {}) if w.factory_config else {}
        )
        if config.get("config_dir"):
            summary = FactoryModels(Path(str(config["config_dir"]))).summary()
            models = [m["ref"] for p in summary["providers"] for m in p["models"]]
    except (
        Exception
    ):  # noqa: BLE001 - a card without models still describes the factory
        models = []
    security = project.get("security") or {}
    return {
        "factory": name,
        "purpose": profile.get("purpose", ""),
        "owner": profile.get("owner", ""),
        "sponsor": profile.get("sponsor", ""),
        "intended_use": {
            "data": profile.get("data", ""),
            "affected": profile.get("affected", ""),
            "decides": profile.get("decides", ""),
        },
        "classification": profile.get("classification"),
        "heightened_risk": profile.get("heightened_risk"),
        "labels_decision": (
            {
                "decided_by": labels["decided_by"],
                "decided_at": labels["decided_at"],
                "reasoning": labels["reasoning"] or labels["reason"],
            }
            if labels
            else None
        ),
        "models": models,
        "integrations": [
            i.get("type")
            for i in project.get("integrations") or []
            if isinstance(i, dict)
        ],
        "mcp_servers": (
            sorted((project.get("mcp_servers") or {}).keys())
            if isinstance(project.get("mcp_servers"), dict)
            else [
                m.get("name")
                for m in project.get("mcp_servers") or []
                if isinstance(m, dict)
            ]
        ),
        "allowed_services": [s["name"] for s in g.services()],
        "approval_required_tools": list(security.get("human_approval_tools") or []),
        "retention": "Conversations for the life of the factory; backups 30 days",
        "policy": w.policy.name,
        "last_material_change": (
            {"summary": material[-1]["summary"], "at": material[-1]["decided_at"]}
            if material
            else None
        ),
        "generated_at": time.time(),
    }


def card_markdown(card: dict) -> str:
    use = card["intended_use"]
    decision = card["labels_decision"]
    lines = [
        f"## {card['factory']}",
        "",
        f"- **Purpose:** {card['purpose'] or '—'}",
        f"- **Owner:** {card['owner'] or '—'}",
        f"- **Sponsor:** {card['sponsor'] or '—'}",
        f"- **Data it touches:** {use['data'] or '—'}",
        f"- **Who is affected:** {use['affected'] or '—'}",
        f"- **What the AI does and does not decide:** {use['decides'] or '—'}",
        f"- **Data classification:** {card['classification'] or 'unclassified'}",
        f"- **Heightened risk:** {'yes' if card['heightened_risk'] else 'no' if card['heightened_risk'] is False else 'undetermined'}",
    ]
    if decision:
        lines.append(
            f"- **Classification decided by:** {decision['decided_by']}, {stamp(decision['decided_at'])}: {decision['reasoning'] or '—'}"
        )
    lines += [
        f"- **Models:** {', '.join(card['models']) or '—'}",
        f"- **Integrations:** {', '.join(filter(None, card['integrations'])) or '—'}",
        f"- **MCP servers:** {', '.join(filter(None, card['mcp_servers'])) or '—'}",
        f"- **Tools that always need a person's approval:** {', '.join(card['approval_required_tools']) or '—'}",
        f"- **Retention:** {card['retention']}",
        f"- **Last material change:** "
        + (
            f"{card['last_material_change']['summary']} ({stamp(card['last_material_change']['at'])})"
            if card["last_material_change"]
            else "—"
        ),
    ]
    return "\n".join(lines) + "\n"


# -- scheduled reviews -------------------------------------------------------


class Reviews:
    """The governance calendar: what is due, and every completion."""

    def __init__(self, store: Any, audit_log: Any) -> None:
        self.store = store
        self.audit_log = audit_log

    def completions(self) -> List[dict]:
        return sorted(
            (doc for _, doc in self.store.docs(REVIEW_KIND)), key=lambda d: d["at"]
        )

    def complete(
        self, kind: str, by: str, result: str, factory: Optional[str] = None
    ) -> dict:
        if kind not in {r[0] for r in REVIEWS}:
            raise ValueError("Unknown review")
        import uuid

        record = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "factory": factory,
            "by": by,
            "at": time.time(),
            "result": result,
        }
        self.store.put(REVIEW_KIND, record["id"], record)
        self.audit_log.record(
            "governance.review_completed",
            actor=by,
            kind=kind,
            review_factory=factory,
            result=result,
        )
        return record

    def calendar(
        self, factories: Iterable[dict], now: Optional[float] = None
    ) -> List[dict]:
        """Each review's last completion, when it is next due, and whether overdue.
        ``factories``: ``{id, name, heightened_risk}``."""
        now = now or time.time()
        done = self.completions()
        out = []
        for kind, title, days, by, per_factory in REVIEWS:
            targets: List[Optional[dict]] = [None]
            if per_factory:
                targets = [
                    f
                    for f in factories
                    if kind != "heightened_risk_reassessment"
                    or f.get("heightened_risk")
                ]
            for target in targets:
                factory = target["id"] if target else None
                last = next(
                    (
                        d
                        for d in reversed(done)
                        if d["kind"] == kind and d["factory"] == factory
                    ),
                    None,
                )
                due = (last["at"] + days * DAY) if last else now
                out.append(
                    {
                        "kind": kind,
                        "title": title + (f": {target['name']}" if target else ""),
                        "factory": factory,
                        "performed_by": by,
                        "every_days": days,
                        "last": last,
                        "due_at": due,
                        "overdue": due <= now,
                    }
                )
        return out


# -- reports -----------------------------------------------------------------


def quarter_bounds(quarter: str) -> tuple[float, float]:
    """``2026Q4`` → its start and end, in seconds."""
    year, q = int(quarter[:4]), int(quarter[-1])
    if not 1 <= q <= 4 or quarter[4:5].upper() != "Q":
        raise ValueError("A quarter is written like 2026Q4")
    start = datetime(year, 3 * q - 2, 1, tzinfo=timezone.utc)
    end = datetime(year + (q == 4), 1 if q == 4 else 3 * q + 1, 1, tzinfo=timezone.utc)
    return start.timestamp(), end.timestamp()


def collect(sources: List[dict], since: float, until: float) -> dict:
    """Records in ``[since, until)`` from each source ``{name, decisions, audit_log}``."""
    decisions, events = [], []
    for source in sources:
        decisions += [
            {**d, "source": source["name"]}
            for d in source["decisions"].all(since, until)
        ]
        events += [
            {**e, "source": source["name"]}
            for e in source["audit_log"].events(since, until)
        ]
    return {
        "decisions": sorted(decisions, key=lambda d: d["proposed_at"]),
        "audit": sorted(events, key=lambda e: e["at"]),
    }


def quarterly_report(
    title: str,
    quarter: str,
    sources: List[dict],
    cards: List[dict],
    calendar: List[dict],
    reviews: List[dict],
) -> str:
    since, until = quarter_bounds(quarter)
    records = collect(sources, since, until)
    decisions = [d for d in records["decisions"] if d["status"] != "pending"]
    lowered = [
        d for d in decisions if d["kind"] == "labels" and d["status"] == "decided"
    ]
    material = [d for d in decisions if d["kind"] in MATERIAL_KINDS - {"labels"}]
    stops = [e for e in records["audit"] if e["event"] in KILL_SWITCH]
    alerts = [e for e in records["audit"] if e["event"].startswith("alert.")]
    done = [r for r in reviews if since <= r["at"] < until]
    overdue = [c for c in calendar if c["overdue"]]
    lines = [
        f"# {title}: AI governance report, {quarter}",
        "",
        f"Generated {stamp(time.time())} from Workplace's records.",
        "",
        "## Factories",
        "",
    ]
    lines += [
        f"- **{c['factory']}**: {c['classification'] or 'unclassified'}, "
        f"{'heightened risk' if c['heightened_risk'] else 'not heightened risk'}; "
        f"sponsor {c['sponsor'] or '—'}; {c['purpose'] or 'no purpose recorded'}"
        for c in cards
    ] or ["None."]
    lines += ["", "## Governance decisions", ""]
    lines += [
        f"- {stamp(d['decided_at'])}: {d['summary']} — decided by {d['decided_by']}"
        + (
            f": {d['reasoning'] or d['reason']}"
            if (d["reasoning"] or d["reason"])
            else ""
        )
        + (" (declined)" if d["status"] == "declined" else "")
        for d in decisions
    ] or ["None."]
    lines += ["", "## Classifications or risk lowered", ""]
    lines += [
        f"- {d['summary']} — {d['decided_by']}: {d['reasoning'] or '—'}"
        for d in lowered
    ] or ["None."]
    lines += ["", "## Material changes", ""]
    lines += [f"- {stamp(d['decided_at'])}: {d['summary']}" for d in material] or [
        "None."
    ]
    lines += ["", "## Kill switch", ""]
    lines += [
        f"- {stamp(e['at'])}: {e['event']} by {e.get('actor') or '—'}: {e.get('reason', '')}"
        for e in stops
    ] or ["Not used."]
    lines += ["", "## Alerts", ""]
    lines += [f"- {stamp(e['at'])}: {e['event']}" for e in alerts] or ["None."]
    lines += ["", "## Scheduled reviews done", ""]
    lines += [
        f"- {stamp(r['at'])}: {r['kind']} by {r['by']}: {r['result']}" for r in done
    ] or ["None."]
    lines += ["", "## Overdue", ""]
    lines += [f"- {c['title']} (due {stamp(c['due_at'])})" for c in overdue] or [
        "Nothing is overdue."
    ]
    return "\n".join(lines) + "\n"


def evidence_export(
    sources: List[dict],
    cards: List[dict],
    calendar: List[dict],
    reviews: List[dict],
    since: float,
    until: float,
    quarters: Dict[str, str],
) -> dict:
    """Everything a review or audit asks for, in a date range: metadata only."""
    records = collect(sources, since, until)
    audit = records["audit"]
    return {
        "generated_at": time.time(),
        "range": {"since": since, "until": until},
        "inventory": cards,
        "decisions": records["decisions"],
        "quarterly_reports": quarters,
        "change_history": [
            e
            for e in audit
            if e["event"].endswith("_changed")
            or e["event"] in ("governance.decision", "factory.review_changed")
        ],
        "kill_switch": [e for e in audit if e["event"] in KILL_SWITCH],
        "named_tool_approvals": [
            e for e in audit if e["event"].startswith("approval.")
        ],
        "backups_and_restores": [r for r in reviews if r["kind"] == "restore_drill"]
        + [e for e in audit if e["event"].startswith("backup.")],
        "access_reviews": [r for r in reviews if r["kind"] == "access_review"]
        + [e for e in audit if e["event"] == "access_review.exported"],
        "scheduled_reviews": {
            "calendar": calendar,
            "completed": [r for r in reviews if since <= r["at"] < until],
        },
        "alerts": [e for e in audit if e["event"].startswith("alert.")],
        "audit_log": audit,
    }


async def notify_overdue(
    calendar, notifier, store, key: str, interval: float = 3600
) -> None:
    """Post overdue reviews to the Teams channel, once a day."""
    import asyncio
    import logging

    log = logging.getLogger(__name__)
    while True:
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if store.meta(key) != today:
                overdue = [c for c in calendar() if c["overdue"]]
                if overdue:
                    await notifier.send(
                        "Governance reviews overdue",
                        f"{len(overdue)} scheduled review(s) are overdue.",
                        [(c["title"], "due " + stamp(c["due_at"])) for c in overdue],
                    )
                store.set_meta(key, today)
        except Exception:  # noqa: BLE001 - the loop outlives a failure
            log.exception("governance: overdue reminder failed")
        await asyncio.sleep(interval)
