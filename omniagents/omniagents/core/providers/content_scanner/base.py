"""Base content scanner interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Finding:
    """A single sensitive-data detection."""

    pattern_name: str
    matched_text: str  # the redacted/masked version for logging
    start: int = 0
    end: int = 0


@dataclass
class ScanResult:
    """Result of scanning content for sensitive data."""

    allowed: bool = True
    findings: List[Finding] = field(default_factory=list)
    redacted_content: Optional[str] = None


class ContentScanner(ABC):
    """Base class for content scanning providers.

    Scans text for sensitive data (PHI, PII, credentials) and
    optionally redacts findings.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    async def scan(self, content: str, direction: str = "output") -> ScanResult:
        """Scan *content* for sensitive data.

        *direction* is ``"input"`` or ``"output"`` to indicate whether
        the content comes from the user or the model.

        Returns a :class:`ScanResult` with findings and optional redaction.
        """
        ...
