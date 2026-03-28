"""No-op content scanner (default)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from omniagents.core.providers.content_scanner.base import ContentScanner, ScanResult


class NoneScanner(ContentScanner):
    """Passes all content through without scanning."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config or {})

    async def scan(self, content: str, direction: str = "output") -> ScanResult:
        return ScanResult(allowed=True)
