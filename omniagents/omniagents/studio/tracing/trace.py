"""Enhanced trace context manager for OmniAgents Studio tracing."""

from contextlib import contextmanager
import logging
from typing import Any, Optional

from agents.tracing import trace as sdk_trace
from agents.tracing import add_trace_processor

from .processor import StudioTraceProcessor, AgentIOProcessor

logger = logging.getLogger(__name__)


@contextmanager
def trace(
    workflow_name: str,
    trace_id: Optional[str] = None,
    group_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    disabled: bool = False,
    project: Optional[str] = None,
    server_url: Optional[str] = None,
    token: Optional[str] = None,
):
    """Augment trace metadata with Studio project routing."""
    from . import _default_project, _processor

    target_project = project or _default_project

    if not target_project and not _processor:
        with sdk_trace(workflow_name, trace_id, group_id, metadata, disabled=True):
            yield
        return

    if not _processor and target_project:
        processor = StudioTraceProcessor(default_project=None)
        add_trace_processor(processor)

    enhanced_metadata = dict(metadata or {})

    if target_project:
        enhanced_metadata["studio_project"] = target_project
        enhanced_metadata.setdefault("agentio_project", target_project)

    if server_url:
        logger.warning("Remote tracing in trace() is not implemented; using local storage")

    with sdk_trace(workflow_name, trace_id, group_id, enhanced_metadata, disabled):
        yield


__all__ = ["trace"]
