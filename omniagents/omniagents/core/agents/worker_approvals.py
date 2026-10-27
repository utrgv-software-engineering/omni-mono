"""Worker review policy: whose configuration a worker's approvals follow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class WorkerApprovalContext:
    """A worker's link to the service that started it.

    A worker's approvals are always decided without a person: auto-approved
    when ``auto_approve`` is set, otherwise by the guardian, which fails
    closed. ``parent_service`` supplies the approval configuration.
    """

    parent_service: Any
    parent_session_id: str | None
    worker_id: str
    auto_approve: bool = False
