import json
import logging
import os
import threading
from collections import deque
from typing import Any, Dict, Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

from agents.tracing import TracingProcessor

from omniagents.core.paths import get_traces_dir

from .storage import SQLiteStorage
from .utils import fibo, validate_keys

logger = logging.getLogger(__name__)


class StudioTraceProcessor(TracingProcessor):
    """Local trace processor that routes to SQLite databases by project"""

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


class RemoteStudioTraceProcessor(TracingProcessor):
    """Remote trace processor that forwards events to Studio servers"""

    def __init__(self, endpoint: str, project: str, token: Optional[str] = None):
        if httpx is None:
            raise ImportError(
                "httpx is required for remote processor. Install with: pip install httpx"
            )

        self.endpoint = endpoint
        self.project = project
        self.token = token or os.environ.get("OMNIAGENTS_STUDIO_TOKEN")
        self.client: Optional[Any] = None
        self._client_lock = threading.Lock()
        self._queued_events: deque = deque(maxlen=1000)
        self._shutdown = False
        self._client_thread = threading.Thread(target=self._init_client_with_retry)
        self._client_thread.daemon = True
        self._client_thread.start()

    def _init_client_with_retry(self):
        import time

        fib = fibo()
        for sleep_coefficient in fib:
            if self._shutdown:
                break

            try:
                client = httpx.Client(timeout=httpx.Timeout(5.0))
                test_url = self.endpoint.replace("/api/trace", "/").replace(
                    "/trace", "/"
                )
                client.get(test_url, timeout=2.0)

                with self._client_lock:
                    self.client = client
                    while self._queued_events and not self._shutdown:
                        event = self._queued_events.popleft()
                        self._send_now(event["type"], event["data"])
                    logger.info(
                        "Connected to OmniAgents Studio tracing server: %s",
                        self.endpoint,
                    )
                    break
            except Exception as exc:
                logger.debug("Failed to connect to server: %s", exc)
                if sleep_coefficient and not self._shutdown:
                    time.sleep(0.1 * sleep_coefficient)

    def on_trace_start(self, trace: Any):
        try:
            trace_data = trace.export()
            if "metadata" in trace_data:
                validate_keys(trace_data["metadata"])
            self._send("trace_start", trace_data)
        except Exception as exc:
            logger.debug("Failed to process trace start: %s", exc)

    def on_trace_end(self, trace: Any):
        try:
            trace_data = trace.export()
            self._send("trace_end", {"id": trace_data.get("id")})
        except Exception as exc:
            logger.debug("Failed to process trace end: %s", exc)

    def on_span_start(self, span: Any):
        pass

    def on_span_end(self, span: Any):
        try:
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

            self._send("span_end", span_export)
        except Exception as exc:
            logger.debug("Failed to process span end: %s", exc)

    def _send(self, event_type: str, data: Dict[str, Any]):
        with self._client_lock:
            if self.client is None:
                self._queued_events.append({"type": event_type, "data": data})
            else:
                self._send_now(event_type, data)

    def _send_now(self, event_type: str, data: Dict[str, Any]):
        if self._shutdown or self.client is None:
            return

        try:
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"

            self.client.post(
                self.endpoint,
                json={
                    "event": event_type,
                    "project": self.project,
                    "data": data,
                },
                headers=headers,
                timeout=2.0,
            )
        except Exception as exc:
            logger.debug("Failed to send trace: %s", exc)

    def shutdown(self):
        self._shutdown = True
        if self._client_thread.is_alive():
            self._client_thread.join(timeout=5.0)
        if self.client:
            self.client.close()

    def force_flush(self):
        with self._client_lock:
            while self._queued_events and self.client and not self._shutdown:
                event = self._queued_events.popleft()
                self._send_now(event["type"], event["data"])


__all__ = [
    "StudioTraceProcessor",
    "RemoteStudioTraceProcessor",
]
