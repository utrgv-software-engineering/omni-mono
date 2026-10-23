import ast
from pathlib import Path
import re

# A request: `call('x'` / `this.call('x'` / `request('x'`.
CALL_PATTERN = re.compile(
    r"(?:this\.)?(?:call|request)(?:<[^>]+>)?\(\s*['\"]([a-z_]+)['\"]"
)
HANDLER_PATTERN = re.compile(r"\.on\(\s*['\"]([a-z_]+)['\"]")


def discover_agent_service_methods(path: Path) -> set[str]:
    module = ast.parse(path.read_text())
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "AgentService":
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise ValueError(f"AgentService not found in {path}")


def discover_client_rpc_calls(paths: list[Path]) -> set[str]:
    return _discover(paths, CALL_PATTERN)


def discover_notification_handlers(paths: list[Path]) -> set[str]:
    return _discover(paths, HANDLER_PATTERN)


def _discover(paths: list[Path], pattern: re.Pattern[str]) -> set[str]:
    discovered = set()
    for path in paths:
        discovered.update(pattern.findall(path.read_text()))
    return discovered


def missing_from_catalog(discovered: set[str], catalog: set[str]) -> set[str]:
    return discovered - catalog
