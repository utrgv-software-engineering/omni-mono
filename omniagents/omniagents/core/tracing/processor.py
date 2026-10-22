"""Local trace processor: records SDK traces and spans into per-project
SQLite databases under ``get_traces_dir()`` (the store Studio reads)."""

import logging
import threading
from typing import Any, Dict, Optional

from agents.tracing import TracingProcessor

from omniagents.core.paths import get_traces_dir

from .storage import SQLiteStorage
from .utils import validate_keys

logger = logging.getLogger(__name__)


class LocalTraceProcessor(TracingProcessor):
    """Routes traces to per-project SQLite databases (``studio_project`` metadata)."""

    def __init__(self, default_project: Optional[str] = None):
        self.default_project = default_project
        self._storages: Dict[str, SQLiteStorage] = {}
        self._lock = threading.Lock()

    def _get_project_from_trace(self, trace: Any) -> str:
        if hasattr(trace, "metadata") and trace.metadata:
            project = trace.metadata.get("studio_project")
            if project:
                return project
        return self.default_project or "default"

    def _get_storage(self, project: str) -> SQLiteStorage:
        with self._lock:
            if project not in self._storages:
                db_path = get_traces_dir() / f"{project}.db"
                self._storages[project] = SQLiteStorage(str(db_path))
            return self._storages[project]

    def on_trace_start(self, trace: Any):
        try:
            project = self._get_project_from_trace(trace)
            storage = self._get_storage(project)
            trace_data = trace.export()
            if "metadata" in trace_data:
                validate_keys(trace_data["metadata"])
            storage.insert_trace(trace_data)
        except Exception as exc:
            logger.debug("Failed to insert trace: %s", exc)

    def on_trace_end(self, trace: Any):
        pass

    def on_span_start(self, span: Any):
        pass

    def on_span_end(self, span: Any):
        try:
            from agents.tracing import get_current_trace

            trace = get_current_trace()
            if not trace:
                logger.debug("No active trace for span")
                return

            project = self._get_project_from_trace(trace)
            storage = self._get_storage(project)
            span_export = span.export()

            if hasattr(span, "span_data"):
                span_data = span.span_data
                span_type = getattr(span_data, "type", None)

                if span_type == "response" and hasattr(span_data, "response"):
                    response = span_data.response
                    if response:
                        if hasattr(response, "model_dump"):
                            enhanced_response = response.model_dump()
                        else:
                            enhanced_response = (
                                dict(response)
                                if hasattr(response, "__dict__")
                                else str(response)
                            )
                        span_export.setdefault("span_data", {})[
                            "response"
                        ] = enhanced_response
                        if hasattr(span_data, "input") and span_data.input:
                            span_export["span_data"]["input"] = span_data.input
                elif (
                    span_type == "generation"
                    and hasattr(span_data, "input")
                    and span_data.input
                ):
                    span_export.setdefault("span_data", {})["input"] = span_data.input

            storage.insert_span(span_export)
        except Exception as exc:
            logger.debug("Failed to insert span: %s", exc)

    def shutdown(self):
        with self._lock:
            for storage in self._storages.values():
                try:
                    storage.close()
                except Exception:
                    pass
            self._storages.clear()

    def force_flush(self):
        pass


__all__ = ["LocalTraceProcessor"]
