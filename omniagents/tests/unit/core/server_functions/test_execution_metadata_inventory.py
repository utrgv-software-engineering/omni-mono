"""Exhaustive execution-metadata inventory for framework server functions.

This suite intentionally inspects source rather than importing the builtin
modules.  The guardrail therefore catches a missing declaration even when a
capability is disabled, optional dependencies are unavailable, or discovery
would otherwise omit the module at runtime.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope

REPO_ROOT = Path(__file__).resolve().parents[4]
BUILTIN_ROOT = REPO_ROOT / "omniagents" / "builtin" / "server_functions"
SERVICE_SOURCE = REPO_ROOT / "omniagents" / "core" / "agents" / "service.py"


Classification = tuple[ResourceScope, ExecutionRequirement]


def _classification(
    scope: ResourceScope, requirements: ExecutionRequirement = ExecutionRequirement.NONE
) -> Classification:
    return scope, requirements


# This is deliberately an exact inventory rather than a set of broad naming
# rules.  A new server function must make an explicit architecture decision,
# and aliases remain independently visible protocol extensions that cannot
# silently drift from their canonical function.
EXPECTED_BUILTIN_CLASSIFICATIONS: dict[str, Classification] = {
    "artifact": _classification(ResourceScope.THREAD),
    "bash_jobs.list": _classification(
        ResourceScope.THREAD, ExecutionRequirement.PROCESS
    ),
    "bash_jobs.kill": _classification(
        ResourceScope.THREAD, ExecutionRequirement.PROCESS
    ),
    "bash_jobs.tail": _classification(
        ResourceScope.THREAD,
        ExecutionRequirement.PROCESS | ExecutionRequirement.WORKSPACE_READ,
    ),
    "bj-list": _classification(ResourceScope.THREAD, ExecutionRequirement.PROCESS),
    "bj-kill": _classification(ResourceScope.THREAD, ExecutionRequirement.PROCESS),
    "bj-logs": _classification(
        ResourceScope.THREAD,
        ExecutionRequirement.PROCESS | ExecutionRequirement.WORKSPACE_READ,
    ),
    "compact": _classification(ResourceScope.THREAD),
    "good": _classification(ResourceScope.THREAD),
    "bad": _classification(ResourceScope.THREAD),
    "note": _classification(ResourceScope.THREAD),
    "notes": _classification(ResourceScope.THREAD),
    "goal.stop": _classification(ResourceScope.THREAD),
    "goal.status": _classification(ResourceScope.THREAD),
    "goal.pause": _classification(ResourceScope.THREAD),
    "goal.resume": _classification(ResourceScope.THREAD),
    "goal": _classification(ResourceScope.THREAD),
    "help": _classification(ResourceScope.AGENT_HOST),
    "loop": _classification(
        ResourceScope.THREAD, ExecutionRequirement.OPTIONAL_WORKSPACE_READ
    ),
    "loop.status": _classification(ResourceScope.THREAD),
    "loop.stop": _classification(ResourceScope.THREAD),
    "loop.clear": _classification(ResourceScope.THREAD),
    "cron.create": _classification(ResourceScope.THREAD),
    "cron.list": _classification(ResourceScope.THREAD),
    "cron.delete": _classification(ResourceScope.THREAD),
    "models": _classification(ResourceScope.THREAD),
    "model": _classification(ResourceScope.THREAD),
    "reasoning": _classification(ResourceScope.THREAD),
    "voice-models": _classification(ResourceScope.AGENT),
    "voice-model": _classification(ResourceScope.AGENT),
    "recap": _classification(ResourceScope.THREAD),
    "sandbox.pause": _classification(
        ResourceScope.ENVIRONMENT, ExecutionRequirement.ENVIRONMENT_LIFECYCLE
    ),
    "sandbox.unpause": _classification(
        ResourceScope.ENVIRONMENT, ExecutionRequirement.ENVIRONMENT_LIFECYCLE
    ),
    "sandbox.discard_snapshot": _classification(
        ResourceScope.ENVIRONMENT, ExecutionRequirement.ENVIRONMENT_LIFECYCLE
    ),
    "sandbox.set_network": _classification(
        ResourceScope.ENVIRONMENT, ExecutionRequirement.ENVIRONMENT_LIFECYCLE
    ),
    "sandbox.get_network": _classification(
        ResourceScope.ENVIRONMENT, ExecutionRequirement.ENVIRONMENT_LIFECYCLE
    ),
    "terminal.create": _classification(ResourceScope.THREAD, ExecutionRequirement.PTY),
    "terminal.close": _classification(ResourceScope.THREAD, ExecutionRequirement.PTY),
    "wakeup.stop": _classification(ResourceScope.THREAD),
    "wakeup.status": _classification(ResourceScope.THREAD),
    "workers.list": _classification(ResourceScope.THREAD),
    "workers.kill": _classification(ResourceScope.THREAD),
    "fs_get_cwd": _classification(
        ResourceScope.WORKSPACE, ExecutionRequirement.WORKSPACE_READ
    ),
    "fs_get_workspace_root": _classification(
        ResourceScope.WORKSPACE, ExecutionRequirement.WORKSPACE_READ
    ),
    "fs_get_home": _classification(
        ResourceScope.ENVIRONMENT, ExecutionRequirement.PROCESS
    ),
    "fs_get_sources": _classification(
        ResourceScope.WORKSPACE, ExecutionRequirement.WORKSPACE_READ
    ),
    "fs_list_dir": _classification(
        ResourceScope.WORKSPACE, ExecutionRequirement.WORKSPACE_READ
    ),
}


EXPECTED_CORE_CLASSIFICATIONS: dict[str, Classification] = {
    "session.ensure": _classification(ResourceScope.THREAD),
    "mcp.list_resources": _classification(ResourceScope.THREAD),
    "mcp.read_resource": _classification(ResourceScope.THREAD),
    "mcp.list_tools": _classification(ResourceScope.THREAD),
    "mcp.call_tool": _classification(ResourceScope.THREAD),
}


@dataclass(frozen=True)
class DecoratedServerFunction:
    published_name: str
    function_name: str
    path: Path
    line: int
    keywords: dict[str, ast.expr]

    @property
    def location(self) -> str:
        return f"{self.path.relative_to(REPO_ROOT)}:{self.line}"


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_string(node: ast.expr, *, location: str, field: str) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise AssertionError(f"{location}: {field} must be a string literal")


def _published_name(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    keywords: dict[str, ast.expr],
    *,
    location: str,
) -> str:
    for field in ("name_override", "name"):
        value = keywords.get(field)
        if value is not None:
            return _literal_string(value, location=location, field=field)
    return function.name


def _decorated_builtins() -> list[DecoratedServerFunction]:
    declarations: list[DecoratedServerFunction] = []
    for path in sorted(BUILTIN_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                callable_node = call.func if call is not None else decorator
                if _call_name(callable_node) != "server_function":
                    continue
                keywords = (
                    {
                        keyword.arg: keyword.value
                        for keyword in call.keywords
                        if keyword.arg
                    }
                    if call is not None
                    else {}
                )
                location = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
                declarations.append(
                    DecoratedServerFunction(
                        published_name=_published_name(
                            node, keywords, location=location
                        ),
                        function_name=node.name,
                        path=path,
                        line=node.lineno,
                        keywords=keywords,
                    )
                )
    return declarations


def _enum_member(
    node: ast.expr,
    enum_type: type[ResourceScope] | type[ExecutionRequirement],
    location: str,
):
    if (
        not isinstance(node, ast.Attribute)
        or not isinstance(node.value, ast.Name)
        or node.value.id != enum_type.__name__
    ):
        raise AssertionError(
            f"{location}: expected explicit {enum_type.__name__}.<member>, "
            f"got {ast.unparse(node)}"
        )
    try:
        return enum_type[node.attr]
    except KeyError as exc:
        raise AssertionError(
            f"{location}: unknown {enum_type.__name__} member {node.attr}"
        ) from exc


def _execution_requirements(node: ast.expr, location: str) -> ExecutionRequirement:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _execution_requirements(node.left, location) | _execution_requirements(
            node.right, location
        )
    return _enum_member(node, ExecutionRequirement, location)


def _declared_classification(declaration: DecoratedServerFunction) -> Classification:
    missing = {
        field
        for field in ("resource_scope", "execution_requirements")
        if field not in declaration.keywords
    }
    assert not missing, (
        f"{declaration.location}: @{declaration.function_name} must explicitly declare "
        f"{', '.join(sorted(missing))}"
    )
    return (
        _enum_member(
            declaration.keywords["resource_scope"],
            ResourceScope,
            declaration.location,
        ),
        _execution_requirements(
            declaration.keywords["execution_requirements"], declaration.location
        ),
    )


def _literal_core_registrations() -> dict[str, tuple[dict[str, ast.expr], str]]:
    tree = ast.parse(
        SERVICE_SOURCE.read_text(encoding="utf-8"), filename=str(SERVICE_SOURCE)
    )
    registrations: dict[str, tuple[dict[str, ast.expr], str]] = {}
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or _call_name(node.func) != "register_server_function"
        ):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if not isinstance(node.args[0].value, str):
            continue
        name = node.args[0].value
        registrations[name] = (
            {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg},
            f"{SERVICE_SOURCE.relative_to(REPO_ROOT)}:{node.lineno}",
        )
    return registrations


def test_all_47_decorated_builtins_have_the_expected_execution_metadata():
    declarations = _decorated_builtins()
    by_name = {declaration.published_name: declaration for declaration in declarations}

    assert len(declarations) == 47
    assert len(by_name) == len(
        declarations
    ), "published server-function names must be unique"
    assert set(by_name) == set(EXPECTED_BUILTIN_CLASSIFICATIONS)

    actual = {
        name: _declared_classification(declaration)
        for name, declaration in by_name.items()
    }
    assert actual == EXPECTED_BUILTIN_CLASSIFICATIONS


def test_builtin_aliases_have_the_same_classification_as_their_canonical_name():
    expected_aliases = {
        "bj-list": "bash_jobs.list",
        "bj-kill": "bash_jobs.kill",
        "bj-logs": "bash_jobs.tail",
    }

    for alias, canonical in expected_aliases.items():
        assert (
            EXPECTED_BUILTIN_CLASSIFICATIONS[alias]
            == EXPECTED_BUILTIN_CLASSIFICATIONS[canonical]
        )


def test_five_always_registered_core_functions_declare_thread_metadata():
    registrations = _literal_core_registrations()

    assert set(registrations) == set(EXPECTED_CORE_CLASSIFICATIONS)
    for name, expected in EXPECTED_CORE_CLASSIFICATIONS.items():
        keywords, location = registrations[name]
        missing = {
            field
            for field in ("resource_scope", "execution_requirements")
            if field not in keywords
        }
        assert not missing, f"{location}: {name} is missing {sorted(missing)}"
        actual = (
            _enum_member(keywords["resource_scope"], ResourceScope, location),
            _execution_requirements(keywords["execution_requirements"], location),
        )
        assert actual == expected, f"{location}: unexpected metadata for {name}"


def test_capability_registration_forwards_decorator_execution_metadata():
    """The dynamic registration path must not drop decorator metadata."""

    tree = ast.parse(
        SERVICE_SOURCE.read_text(encoding="utf-8"), filename=str(SERVICE_SOURCE)
    )
    dynamic_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node.func) == "register_server_function"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "name"
    ]

    assert len(dynamic_calls) == 1
    call = dynamic_calls[0]
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
    for field in ("resource_scope", "execution_requirements"):
        expression = keywords.get(field)
        assert isinstance(expression, ast.Call), (
            f"{SERVICE_SOURCE.relative_to(REPO_ROOT)}:{call.lineno}: "
            f"dynamic registration must forward {field}"
        )
        assert (
            isinstance(expression.func, ast.Attribute)
            and isinstance(expression.func.value, ast.Name)
            and expression.func.value.id == "meta"
            and expression.func.attr == "get"
            and expression.args
            and isinstance(expression.args[0], ast.Constant)
            and expression.args[0].value == field
        )


def test_environment_backed_server_function_modules_have_no_host_path_fallbacks():
    """Keep execution adapters—not AgentHost paths—as the filesystem seam.

    This is intentionally static and module-wide. A new environment-backed
    server function may call Workspace/lease APIs, but importing host path or
    subprocess primitives into the same RPC implementation is an architecture
    review failure rather than something a Devbox E2E test should discover.
    """

    environment_requirements = (
        ExecutionRequirement.WORKSPACE_READ
        | ExecutionRequirement.WORKSPACE_WRITE
        | ExecutionRequirement.PROCESS
        | ExecutionRequirement.NETWORK
        | ExecutionRequirement.PTY
        | ExecutionRequirement.ENVIRONMENT_LIFECYCLE
        | ExecutionRequirement.OPTIONAL_WORKSPACE_READ
    )
    sensitive_paths = {
        declaration.path
        for declaration in _decorated_builtins()
        if _declared_classification(declaration)[1] & environment_requirements
    }
    violations: list[str] = []
    for path in sorted(sensitive_paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in {"os", "pathlib", "subprocess"}:
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[
                0
            ] in {"os", "pathlib", "subprocess"}:
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}: from {node.module} import ..."
                )
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in {
                    "open",
                    "Path",
                    "workspace_root_from_session",
                    "session_workspace_root",
                }:
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: call {name}()"
                    )

    assert not violations, "\n".join(violations)
