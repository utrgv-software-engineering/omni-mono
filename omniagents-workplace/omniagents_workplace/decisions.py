"""Governance decisions: every one recorded, with who made it and why.

Health Affairs governs its own AI risk. Its **governance owner** decides
technical governance directly: allowed services, a factory's models, material
changes, and lowering a factory's classification or risk. Anyone else's
proposal waits for the governance owner; the governance owner's own decisions
take effect at once. Raising a classification or risk only adds controls and
is never gated. Under the open policy nothing waits, but everything is still
recorded.

A record keeps the change (before and after), who proposed it and why, who
decided, their reasoning, and when. Records are permanent: a decided record is
never edited again. ``Decisions`` holds a factory's (in its ``workplace.db``)
or an installation's (in ``installation.db``).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

KIND = "governance.decision"
PENDING, DECIDED, DECLINED, APPLIED = "pending", "decided", "declined", "applied"


class Decisions:
    def __init__(self, store: Any, audit_log: Any) -> None:
        self.store = store
        self.audit_log = audit_log
        #: Applies an approved pending change, by decision kind.
        self.appliers: Dict[str, Callable[[dict], None]] = {}

    def all(self, since: float = 0, until: Optional[float] = None) -> List[dict]:
        rows = [doc for _, doc in self.store.docs(KIND)]
        rows = [
            r
            for r in rows
            if r["proposed_at"] >= since and (until is None or r["proposed_at"] < until)
        ]
        return sorted(rows, key=lambda r: r["proposed_at"])

    def pending(self) -> List[dict]:
        return [r for r in self.all() if r["status"] == PENDING]

    def get(self, decision_id: str) -> Optional[dict]:
        return dict(self.store.docs(KIND)).get(decision_id)

    def by_key(self, key: str) -> Optional[dict]:
        found = [r for r in self.all() if r.get("key") == key]
        return found[-1] if found else None

    def _save(self, record: dict) -> dict:
        self.store.put(KIND, record["id"], record)
        return record

    def record(
        self,
        *,
        kind: str,
        summary: str,
        change: dict,
        proposer: Optional[str],
        reason: str = "",
        status: str = APPLIED,
        decider: Optional[str] = None,
        reasoning: str = "",
        factory: Optional[str] = None,
        key: Optional[str] = None,
        links: Optional[List[str]] = None,
    ) -> dict:
        now = time.time()
        record = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "summary": summary,
            "change": change,
            "proposed_by": proposer,
            "proposed_at": now,
            "reason": reason,
            "status": status,
            "decided_by": decider if status != PENDING else None,
            "decided_at": now if status != PENDING else None,
            "reasoning": reasoning,
            "factory": factory,
            "key": key,
            "links": links or [],
        }
        self.audit_log.record(
            "governance.decision",
            actor=decider or proposer,
            decision=record["id"],
            kind=kind,
            status=status,
            summary=summary,
        )
        return self._save(record)

    def decide(
        self, decision_id: str, decider: str, approve: bool, reasoning: str
    ) -> dict:
        record = self.get(decision_id)
        if record is None:
            raise HTTPException(404, "No such decision")
        if record["status"] != PENDING:
            raise HTTPException(409, "This decision was already made")
        if approve and record["kind"] in self.appliers:
            self.appliers[record["kind"]](record["change"])
        record.update(
            status=DECIDED if approve else DECLINED,
            decided_by=decider,
            decided_at=time.time(),
            reasoning=reasoning,
        )
        self.audit_log.record(
            "governance.decision",
            actor=decider,
            decision=decision_id,
            kind=record["kind"],
            status=record["status"],
            summary=record["summary"],
        )
        return self._save(record)


def decide_or_propose(
    decisions: Decisions,
    *,
    gated: bool,
    owner: bool,
    account: Any,
    kind: str,
    summary: str,
    change: dict,
    reason: str,
    apply: Callable[[], None],
    factory: Optional[str] = None,
) -> dict:
    """Apply now and record it, or record a proposal for the governance owner."""
    if not gated or owner:
        if gated and not reason.strip():
            raise HTTPException(422, "Give the decision's reasoning")
        apply()
        return decisions.record(
            kind=kind,
            summary=summary,
            change=change,
            proposer=account.username,
            reason=reason,
            status=DECIDED if gated else APPLIED,
            decider=account.username,
            reasoning=reason,
            factory=factory,
        )
    return decisions.record(
        kind=kind,
        summary=summary,
        change=change,
        proposer=account.username,
        reason=reason,
        status=PENDING,
        factory=factory,
    )
