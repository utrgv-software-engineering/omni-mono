"""Durable, structured plans — the replacement for the in-memory bridge.

Before this module the agent's plan lived in two module-level dicts in
``omniagents/builtin/tools/task_list_tools.py`` keyed by ``task_scope``,
whose default value is the literal string ``"main"``. That is a process
singleton: every session served by one ``omniagents serve`` process shared
one task list, so two concurrent conversations overwrote each other's plan,
a restart erased it, and a reconnecting client could only recover whatever
``tasks_snapshot`` happened to still be attached to a retained
``tool_result``. None of those are edge cases — they are the normal
multi-session server topology.

The replacement keys a plan on ``(thread_id, scope)`` and persists it as a
canonical :class:`~omniagents.core.conversation.model.Item` of kind
``plan``, marked ``long_lived`` so the ``run_end`` sweep does not force it
terminal (a plan legitimately accumulates steps across many turns — see
:attr:`~omniagents.core.conversation.model.Item.long_lived`).

Three properties this shape buys, all of which the singleton could not:

**Stable identity.** ``plan_id`` is the item id, derived deterministically
from ``(thread_id, natural key)``. It survives process restarts, is
reproducible by a client, and is thread-scoped by construction, so one
session's plan can never address — let alone supersede — another's.

**Structured state.** Steps carry a closed ``status`` enum
(``pending`` / ``in_progress`` / ``completed`` / ``blocked``) plus explicit
dependency edges. A client renders progress by reading fields; it never
parses assistant prose.

**Generations.** A plan is *not* immortal: when the conversation is
archived the live plan lands terminally (see
:meth:`~omniagents.core.conversation.recorder.ConversationRecorder
.finalize_plans`). Because an item's terminal state is authoritative and
immutable, a later plan in the same scope needs a new identity — so the
natural key carries a generation ordinal (``plan:main``, ``plan:main#1``,
…). :meth:`PlanStore.current_key` probes generations in order and returns
the first that is absent or still open, which keeps the id stable for the
whole life of one plan while allowing a scope to be re-planned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .model import Item, ItemStatus, derive_item_id
from .store import ConversationStore

__all__ = [
    "MAX_PLAN_GENERATIONS",
    "PlanGenerationsExhausted",
    "PLAN_STEP_STATUSES",
    "Plan",
    "PlanStep",
    "PlanStore",
    "dependency_map",
    "link",
    "plan_natural_key",
    "unlink",
    "unlink_all",
    "would_cycle",
]

#: The closed set of step statuses. ``blocked`` is first-class rather than
#: inferred from open dependency edges: a step can be blocked by something
#: that is not another step (a missing credential, a third-party outage),
#: and a client that had to *derive* "blocked" from the edge list would
#: disagree with the agent about the state of its own plan.
PLAN_STEP_STATUSES = ("pending", "in_progress", "completed", "blocked")

#: Upper bound on the generation probe. Far beyond any real conversation;
#: exists so a corrupted store cannot spin here (the ``_MAX_OCCURRENCES``
#: precedent in the recorder).
MAX_PLAN_GENERATIONS = 1000


class PlanGenerationsExhausted(RuntimeError):
    """Every plan generation for a ``(thread, scope)`` is already terminal.

    Raised instead of quietly reusing the last generation: writing to a
    terminal item is a no-op, so reusing it would have the tool report
    ``success: true`` for an edit the store never took.
    """

    def __init__(self, thread_id: str, scope: str) -> None:
        super().__init__(
            f"All {MAX_PLAN_GENERATIONS} plan generations for scope {scope!r} on "
            f"thread {thread_id} are terminal"
        )
        self.thread_id = thread_id
        self.scope = scope


def plan_natural_key(scope: str, generation: int = 0) -> str:
    """Natural key for the plan of *scope* at *generation*.

    Generation 0 is unsuffixed so the overwhelmingly common case (one plan
    per scope for the life of a conversation) has the obvious key, and so
    the id is trivially reproducible by a client that has never seen a
    finalized plan.
    """
    return f"plan:{scope}" if generation <= 0 else f"plan:{scope}#{generation}"


@dataclass
class PlanStep:
    """One step of a plan. Every field is structured; none is prose to parse."""

    id: str
    subject: str
    description: str = ""
    #: Present-continuous form shown while the step is in progress
    #: ("Running the test suite"). Falls back to ``subject``.
    active_form: str = ""
    status: str = "pending"
    owner: str = ""
    #: Step ids this step blocks / is blocked by. Symmetric by construction
    #: (see :meth:`PlanStore.link`).
    blocks: List[str] = field(default_factory=list)
    blocked_by: List[str] = field(default_factory=list)
    #: Checkable definition-of-done for this step. Empty means "no semantic
    #: review for this step" — the natural opt-in.
    exit_criteria: str = ""
    #: Outcome of the last completion review:
    #: ``{"outcome": "verified" | "unverified" | "waived",
    #:   "reviewer": "guardian" | "human" | "none", "rationale": str}``.
    #: ``None`` until the step first reaches ``completed``.
    review: Optional[Dict[str, Any]] = None
    #: The criteria-edit ratchet's record: set once, to the pre-edit value,
    #: the first time ``exit_criteria`` changes while the step is
    #: ``in_progress`` (including an edit arriving in the same call that
    #: sets ``completed``). Later edits keep the FIRST original. Empty means
    #: the criteria were never edited mid-step.
    original_exit_criteria: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "active_form": self.active_form or self.subject,
            "status": self.status,
            "owner": self.owner,
            "blocks": list(self.blocks),
            "blocked_by": list(self.blocked_by),
            "exit_criteria": self.exit_criteria,
            "review": dict(self.review) if isinstance(self.review, dict) else None,
            "original_exit_criteria": self.original_exit_criteria,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["PlanStep"]:
        """Rehydrate a step, or ``None`` when the row is unusable.

        Defensive because ``Item.content`` is an open JSON record: a row
        written by a future build (or corrupted) must degrade to "this step
        is not readable" rather than crashing the read path for the whole
        plan.
        """
        if not isinstance(raw, dict):
            return None
        step_id = raw.get("id")
        if not isinstance(step_id, str) or not step_id:
            return None
        status = raw.get("status")

        def text(field: str) -> str:
            value = raw.get(field)
            return value if isinstance(value, str) else ""

        review = raw.get("review")
        return cls(
            id=step_id,
            subject=text("subject"),
            description=text("description"),
            active_form=text("active_form"),
            status=str(status) if status in PLAN_STEP_STATUSES else "pending",
            owner=text("owner"),
            blocks=[
                value for value in raw.get("blocks") or [] if isinstance(value, str)
            ],
            blocked_by=[
                value for value in raw.get("blocked_by") or [] if isinstance(value, str)
            ],
            exit_criteria=text("exit_criteria"),
            review=review if isinstance(review, dict) else None,
            original_exit_criteria=text("original_exit_criteria"),
        )

    def verified(self) -> Optional[bool]:
        """Compact review verdict for clients: ``True`` when the completion
        was verified, ``False`` when it was accepted unverified, ``None``
        otherwise (waived, never reviewed, or not currently completed)."""
        if self.status != "completed" or not isinstance(self.review, dict):
            return None
        outcome = self.review.get("outcome")
        if outcome == "verified":
            return True
        if outcome == "unverified":
            return False
        return None


@dataclass
class Plan:
    """The durable plan for one ``(thread, scope)`` pair."""

    plan_id: str
    thread_id: str
    scope: str
    generation: int
    steps: List[PlanStep] = field(default_factory=list)
    #: Item revision. A client that missed an ``item_updated`` push can tell
    #: a stale cached copy from a current one without diffing the steps.
    revision: int = 0
    #: Item lifecycle status: ``started`` while the plan is live, terminal
    #: once the conversation that owns it has been archived.
    status: str = ItemStatus.STARTED.value
    #: Why a terminal plan landed (``thread_archived``). ``None`` while the
    #: plan is live. On the wire because a history view showing a plan as
    #: ``cancelled`` without saying why is an unexplained failure.
    finalized_by: Optional[str] = None
    updated_at: float = 0.0
    turn_id: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.status == ItemStatus.STARTED.value

    def counts(self) -> Dict[str, int]:
        """Per-status tallies. Always carries every status key, so a client
        can render "0 blocked" without special-casing an absent key."""
        counts = {status: 0 for status in PLAN_STEP_STATUSES}
        for step in self.steps:
            counts[step.status] = counts.get(step.status, 0) + 1
        return counts

    def step(self, step_id: str) -> Optional[PlanStep]:
        for candidate in self.steps:
            if candidate.id == step_id:
                return candidate
        return None

    def next_step_id(self) -> str:
        """Smallest unused positive integer id, as a string.

        Derived from the persisted steps rather than a module-level counter:
        the counter was the other half of the singleton (two sessions minted
        colliding ids), and deriving it means a restored plan continues its
        numbering instead of restarting at 1.
        """
        used = set()
        for step in self.steps:
            try:
                used.add(int(step.id))
            except (TypeError, ValueError):
                continue
        candidate = 1
        while candidate in used:
            candidate += 1
        return str(candidate)

    def open_blockers(self, step: PlanStep) -> List[str]:
        """Ids of *step*'s blockers that are not yet completed."""
        return [
            blocker_id
            for blocker_id in step.blocked_by
            if (other := self.step(blocker_id)) is not None
            and other.status != "completed"
        ]

    def to_content(self) -> Dict[str, Any]:
        """The persisted ``Item.content`` payload."""
        return {
            "plan_id": self.plan_id,
            "scope": self.scope,
            "generation": self.generation,
            "steps": [step.to_dict() for step in self.steps],
            "counts": self.counts(),
        }

    def to_wire(self) -> Dict[str, Any]:
        """The RPC/notification projection: content plus item lifecycle."""
        return {
            **self.to_content(),
            "thread_id": self.thread_id,
            "item_id": self.plan_id,
            "turn_id": self.turn_id,
            "status": self.status,
            "finalized_by": self.finalized_by,
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    def to_summaries(self) -> List[Dict[str, Any]]:
        """Compact per-step view for the legacy ``tasks_snapshot`` channel.

        The web UI and the Ink TUI already read ``tasks_snapshot`` off tool
        ``ui_metadata`` (``App.tsx``, ``useMessagesNew.ts``). Keeping that
        channel — now fed from the durable plan instead of the singleton —
        is what makes this replacement invisible to shipped clients while
        the ``plan`` item and ``item_updated`` become the authoritative
        surface for new ones.
        """
        return [
            {
                "id": step.id,
                "subject": step.subject,
                "activeForm": step.active_form or step.subject,
                "status": step.status,
                "owner": step.owner,
                "blockedBy": self.open_blockers(step),
                "exitCriteria": step.exit_criteria,
                "verified": step.verified(),
                "criteriaEdited": bool(step.original_exit_criteria),
            }
            for step in self.steps
        ]

    @classmethod
    def from_item(cls, item: Item, *, scope: str, generation: int) -> "Plan":
        raw_steps = item.content.get("steps")
        steps: List[PlanStep] = []
        if isinstance(raw_steps, list):
            for entry in raw_steps:
                parsed = PlanStep.from_dict(entry)
                if parsed is not None:
                    steps.append(parsed)
        return cls(
            plan_id=item.item_id,
            thread_id=item.thread_id,
            scope=scope,
            generation=generation,
            steps=steps,
            revision=item.revision,
            status=item.status.value,
            finalized_by=(
                item.content.get("finalized_by")
                if isinstance(item.content.get("finalized_by"), str)
                else None
            ),
            updated_at=item.updated_at,
            turn_id=item.turn_id,
        )


class PlanStore:
    """Read/derive plans from the conversation domain store.

    Deliberately read-only plus pure derivation: every *write* goes through
    :class:`~omniagents.core.conversation.recorder.ConversationRecorder` so
    plans obey the same item lifecycle (deterministic ids, revision bumps,
    ``long_lived`` exemption) as every other conversation object rather than
    inventing a second persistence path.
    """

    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    def plan_id(self, thread_id: str, scope: str, generation: int = 0) -> str:
        return derive_item_id(thread_id, plan_natural_key(scope, generation))

    def current_key(self, thread_id: str, scope: str) -> tuple[str, int]:
        """``(natural_key, generation)`` of the live plan for *scope*.

        Returns the first generation whose item is absent or still open.
        A terminal plan is never reopened — the first terminal state an item
        reaches is authoritative — so the probe steps past it and the next
        plan in that scope gets its own stable id.

        Raises :class:`PlanGenerationsExhausted` when every generation is
        spent. Reusing the last one (the previous behaviour) handed back a
        key whose item is terminal, so the write silently did nothing while
        the tool told the agent it had succeeded.
        """
        for generation in range(MAX_PLAN_GENERATIONS):
            key = plan_natural_key(scope, generation)
            item = self._store.get_item(thread_id, derive_item_id(thread_id, key))
            if item is None or not item.is_terminal:
                return key, generation
        raise PlanGenerationsExhausted(thread_id, scope)

    def _last_key(self, scope: str) -> tuple[str, int]:
        return plan_natural_key(scope, MAX_PLAN_GENERATIONS - 1), (
            MAX_PLAN_GENERATIONS - 1
        )

    def current(self, thread_id: str, scope: str) -> Optional[Plan]:
        """The live plan for *scope*, or ``None`` when there is none yet.

        Reads still answer when generations are exhausted — the last one is
        the most recent state, and refusing to show it would be worse than
        showing it. Only WRITES refuse (see :meth:`current_key`).
        """
        try:
            key, generation = self.current_key(thread_id, scope)
        except PlanGenerationsExhausted:
            key, generation = self._last_key(scope)
        item = self._store.get_item(thread_id, derive_item_id(thread_id, key))
        if item is None:
            return None
        return Plan.from_item(item, scope=scope, generation=generation)

    def empty(self, thread_id: str, scope: str) -> Plan:
        """A plan object for a scope that has never been planned in.

        Callers get the *id the plan will have* before any step exists, so
        a client can subscribe to it and the first ``task_create`` does not
        change the identity out from under them.
        """
        try:
            key, generation = self.current_key(thread_id, scope)
        except PlanGenerationsExhausted:
            key, generation = self._last_key(scope)
        return Plan(
            plan_id=derive_item_id(thread_id, key),
            thread_id=thread_id,
            scope=scope,
            generation=generation,
        )

    def list_plans(self, thread_id: str) -> List[Plan]:
        """Every plan on THIS thread, oldest first, across scopes.

        Used by the history read: a finished conversation must be able to
        show the plan it ran under without the caller knowing which scopes
        or generations existed.

        Scope only separates plans *within* one thread. It does not reach
        worker sub-agents: ``WorkerManager._create_worker`` starts each
        worker with its own ``session_id``
        (``rpc/agents/workers.py``), so a worker's plan lives on the
        worker's own thread and is read with that thread's id. In the
        current topology the scope of a plan is therefore ``"main"`` in
        practice; the dimension exists because the ``task_scope`` context
        key is what a sub-agent spawner sets, and a future in-thread
        sub-agent would land beside its parent rather than on top of it.
        """
        from .model import ItemKind

        plans: List[Plan] = []
        for item in self._store.list_items(
            thread_id, limit=MAX_PLAN_GENERATIONS, kinds=[ItemKind.PLAN]
        ):
            scope = item.content.get("scope")
            generation = item.content.get("generation")
            plans.append(
                Plan.from_item(
                    item,
                    scope=scope if isinstance(scope, str) else "main",
                    generation=generation if isinstance(generation, int) else 0,
                )
            )
        return plans


def _dependency_path(
    deps: Dict[str, List[str]], start: str, target: str
) -> Optional[List[str]]:
    """Chain ``[start, …, target]`` following dependency edges, or ``None``.

    ``deps`` maps a step id to the ids it depends on (its ``blocked_by``).
    Iterative DFS; ids absent from *deps* simply have no outgoing edges.
    """
    stack: List[tuple[str, List[str]]] = [(start, [start])]
    seen: set[str] = set()
    while stack:
        current, path = stack.pop()
        if current == target:
            return path
        if current in seen:
            continue
        seen.add(current)
        for dep in deps.get(current, []):
            stack.append((dep, path + [dep]))
    return None


def dependency_map(plan: Plan) -> Dict[str, List[str]]:
    """``step id → blocked_by`` adjacency for cycle checks (copies lists)."""
    return {step.id: list(step.blocked_by) for step in plan.steps}


def would_cycle(
    deps: Dict[str, List[str]], blocker_id: str, blocked_id: str
) -> Optional[List[str]]:
    """The dependency chain the edge ``blocker → blocked`` would close.

    The edge means *blocked* depends on *blocker*; it creates a cycle
    exactly when *blocker* already (transitively) depends on *blocked*.
    Returns that existing chain ``[blocker, …, blocked]`` — the cycle is
    this chain plus the proposed edge — or ``None`` when the edge is safe.
    Operates on a plain adjacency so callers can simulate a batch of edge
    changes (removals first, additions incrementally) before mutating the
    plan.
    """
    if blocker_id == blocked_id:
        return None  # link() ignores self-edges outright
    return _dependency_path(deps, blocker_id, blocked_id)


def link(plan: Plan, blocker_id: str, blocked_id: str) -> Optional[List[str]]:
    """Record that *blocker_id* must complete before *blocked_id*.

    Writes BOTH edges. The singleton did this too, but only inside one
    scope's dict; here the symmetry is enforced on the plan object so the
    persisted content can never hold a half edge that renders as "blocked
    by nothing" on one side and "blocks nothing" on the other.

    Refuses an edge that would close a dependency cycle and returns the
    would-be cycle's existing chain (``[blocker, …, blocked]``): in an
    all-of precedence model a cycle can never mean iteration — every step
    on it would refuse to start, permanently. Enforced here, not only in
    the tool layer, so persisted plan content can never hold a cycle no
    matter who calls. Unknown ids and self-edges are ignored and return
    ``None``, as before.
    """
    if blocker_id == blocked_id:
        return None
    blocker = plan.step(blocker_id)
    blocked = plan.step(blocked_id)
    if blocker is None or blocked is None:
        return None
    cycle = would_cycle(dependency_map(plan), blocker_id, blocked_id)
    if cycle is not None:
        return cycle
    if blocked_id not in blocker.blocks:
        blocker.blocks.append(blocked_id)
    if blocker_id not in blocked.blocked_by:
        blocked.blocked_by.append(blocker_id)
    return None


def unlink(plan: Plan, blocker_id: str, blocked_id: str) -> None:
    """Remove the ``blocker → blocked`` edge, both directions.

    Silent no-op when either step or the edge is absent — retracting a
    dependency that isn't there is the goal state, not an error (the same
    tolerance ``link`` has for unknown ids). This is the ordinary fix for
    a mis-drawn edge; without it the only recoveries were
    ``overrideBlockers`` (which bypasses the start guard) or deleting the
    step (which destroys its history).
    """
    blocker = plan.step(blocker_id)
    blocked = plan.step(blocked_id)
    if blocker is not None and blocked_id in blocker.blocks:
        blocker.blocks.remove(blocked_id)
    if blocked is not None and blocker_id in blocked.blocked_by:
        blocked.blocked_by.remove(blocker_id)


def unlink_all(plan: Plan, step_id: str) -> None:
    """Detach *step_id* from every neighbour, both directions."""
    for step in plan.steps:
        if step_id in step.blocks:
            step.blocks.remove(step_id)
        if step_id in step.blocked_by:
            step.blocked_by.remove(step_id)
