"""Content scanner provider domain."""

from omniagents.core.providers.content_scanner.base import ContentScanner, ScanResult, Finding
from omniagents.core.providers.content_scanner.none import NoneScanner
from omniagents.core.providers.content_scanner.regex import RegexScanner
from omniagents.core.providers import register_provider

register_provider("content_scanner", "none", NoneScanner)
register_provider("content_scanner", "regex", RegexScanner)

# Presidio — only registered when presidio-analyzer is installed
try:
    from omniagents.core.providers.content_scanner.presidio import PresidioScanner

    register_provider("content_scanner", "presidio", PresidioScanner)
except ImportError:
    pass

__all__ = ["ContentScanner", "ScanResult", "Finding", "NoneScanner", "RegexScanner"]
