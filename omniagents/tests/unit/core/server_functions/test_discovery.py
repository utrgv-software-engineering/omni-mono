import asyncio
import sys
import textwrap
import uuid

import pytest

from omniagents.core.debug import Debug
from omniagents.core.server_functions.discovery import (
    build_server_functions_registrar,
    discover_server_functions_in_dir,
    make_server_functions_registrar_from_dir,
    register_server_functions_from_dir,
    server_function,
)
from omniagents.core.session.manager import Session


def _clear_modules(prefix):
    for mod in list(sys.modules):
        if mod.startswith(prefix):
            sys.modules.pop(mod, None)


@server_function
def strict_echo(name: str, times: int = 1):
    return {"value": name * times}


@server_function(strict=False)
def relaxed(name: str):
    return name


def test_server_function_enforces_strict_payload_validation():
    on_invoke = getattr(strict_echo, "_server_function_on_invoke")
    result = asyncio.run(on_invoke(None, None, {"name": "hi", "times": 2}))
    assert result == {"value": "hihi"}
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(on_invoke(None, None, {"name": "x", "extra": 1}))
    assert "Unexpected parameters" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo_missing:
        asyncio.run(on_invoke(None, None, {}))
    assert "Field required" in str(excinfo_missing.value)


def test_server_function_allows_extra_when_not_strict():
    on_invoke = getattr(relaxed, "_server_function_on_invoke")
    result = asyncio.run(on_invoke(None, None, {"name": "Ada", "extra": 1}))
    assert result == "Ada"


def test_server_function_requires_session_when_annotated():
    @server_function
    async def needs_session(session: Session, token: str):
        return token

    on_invoke = getattr(needs_session, "_server_function_on_invoke")
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(on_invoke(None, None, {"token": "abc"}))
    assert "Session is required" in str(excinfo.value)
    result = asyncio.run(on_invoke(None, object(), {"token": "xyz"}))
    assert result == "xyz"


def test_discover_server_functions_in_dir_warns_on_duplicates(tmp_path, capsys):
    previous = Debug.enabled()
    Debug.set_enabled(True)
    package_name = f"server_pkg_{uuid.uuid4().hex}"
    base_dir = tmp_path / package_name
    base_dir.mkdir()
    (base_dir / "__init__.py").write_text("")
    (base_dir / "alpha.py").write_text(
        textwrap.dedent(
            """
            from omniagents.core.server_functions.discovery import server_function


            @server_function(name="duplicate")
            def one():
                return 1
            """
        )
    )
    (base_dir / "beta.py").write_text(
        textwrap.dedent(
            """
            from omniagents.core.server_functions.discovery import server_function


            @server_function(name="duplicate")
            def two():
                return 2
            """
        )
    )
    try:
        discovered = discover_server_functions_in_dir(str(base_dir))
    finally:
        _clear_modules(package_name)
        Debug.set_enabled(previous)
    assert set(discovered) == {"duplicate"}
    assert discovered["duplicate"]["func"].__name__ in {"one", "two"}
    captured = capsys.readouterr()
    assert "Duplicate server function name" in captured.out


def test_discover_server_functions_in_dir_missing_directory(tmp_path, capsys):
    previous = Debug.enabled()
    Debug.set_enabled(True)
    try:
        discovered = discover_server_functions_in_dir(str(tmp_path / "absent"))
    finally:
        Debug.set_enabled(previous)
    assert discovered == {}
    captured = capsys.readouterr()
    assert "Server function directory" in captured.out


def test_discover_server_functions_in_dir_skips_import_errors(tmp_path, capsys):
    package_name = f"server_pkg_{uuid.uuid4().hex}"
    base_dir = tmp_path / package_name
    base_dir.mkdir()
    (base_dir / "__init__.py").write_text("")
    (base_dir / "broken.py").write_text("import missing_module")
    (base_dir / "valid.py").write_text(
        textwrap.dedent(
            """
            from omniagents.core.server_functions.discovery import server_function


            @server_function
            def ok():
                return "ok"
            """
        )
    )
    try:
        discovered = discover_server_functions_in_dir(str(base_dir))
    finally:
        _clear_modules(package_name)
    assert set(discovered) == {"ok"}
    captured = capsys.readouterr()
    assert "Could not import module" in captured.out


def test_build_server_functions_registrar_registers_metadata():
    metadata = {
        "first": {
            "func": strict_echo,
            "name": "first",
            "description": "desc",
            "params_schema": {"type": "object"},
            "result_schema": {"type": "string"},
            "on_invoke": getattr(strict_echo, "_server_function_on_invoke"),
            "strict": True,
        }
    }
    registrar = build_server_functions_registrar(metadata)

    class DummyService:
        def __init__(self):
            self.calls = []

        def register_server_function(
            self,
            name,
            func,
            description=None,
            params_schema=None,
            result_schema=None,
            on_invoke=None,
            strict=None,
        ):
            self.calls.append(
                (
                    name,
                    func,
                    description,
                    params_schema,
                    result_schema,
                    on_invoke,
                    strict,
                )
            )

    service = DummyService()
    registrar(service)
    assert len(service.calls) == 1
    name, func, description, params_schema, result_schema, on_invoke, strict = (
        service.calls[0]
    )
    assert name == "first"
    assert func is strict_echo
    assert description == "desc"
    assert params_schema == {"type": "object"}
    assert result_schema == {"type": "string"}
    assert on_invoke is getattr(strict_echo, "_server_function_on_invoke")
    assert strict is True


def test_build_server_functions_registrar_rejects_invalid_values():
    with pytest.raises(TypeError):
        build_server_functions_registrar({"bad": object()})


def test_build_server_functions_registrar_handles_registration_errors(capsys):
    registrar = build_server_functions_registrar({"first": strict_echo})

    class FailingService:
        def register_server_function(self, *args, **kwargs):
            raise RuntimeError("boom")

    registrar(FailingService())
    captured = capsys.readouterr()
    assert "Failed to register server function" in captured.out


def test_make_server_functions_registrar_filters_names(tmp_path, capsys):
    package_name = f"server_pkg_{uuid.uuid4().hex}"
    base_dir = tmp_path / package_name
    base_dir.mkdir()
    (base_dir / "__init__.py").write_text("")
    (base_dir / "alpha.py").write_text(
        textwrap.dedent(
            """
            from omniagents.core.server_functions.discovery import server_function


            @server_function
            def alpha():
                return "alpha"
            """
        )
    )
    (base_dir / "beta.py").write_text(
        textwrap.dedent(
            """
            from omniagents.core.server_functions.discovery import server_function


            @server_function
            def beta():
                return "beta"
            """
        )
    )
    try:
        registrar = make_server_functions_registrar_from_dir(
            str(base_dir), ["alpha", "missing", "alpha"]
        )
    finally:
        _clear_modules(package_name)

    class Recorder:
        def __init__(self):
            self.calls = []

        def register_server_function(self, name, *args, **kwargs):
            self.calls.append(name)

    service = Recorder()
    registrar(service)
    assert service.calls == ["alpha"]
    captured = capsys.readouterr()
    assert "server functions were not found" in captured.out


def test_register_server_functions_from_dir_registers_selected(tmp_path):
    package_name = f"server_pkg_{uuid.uuid4().hex}"
    base_dir = tmp_path / package_name
    base_dir.mkdir()
    (base_dir / "__init__.py").write_text("")
    (base_dir / "alpha.py").write_text(
        textwrap.dedent(
            """
            from omniagents.core.server_functions.discovery import server_function


            @server_function
            def alpha():
                return "alpha"
            """
        )
    )
    (base_dir / "beta.py").write_text(
        textwrap.dedent(
            """
            from omniagents.core.server_functions.discovery import server_function


            @server_function
            def beta():
                return "beta"
            """
        )
    )

    class Recorder:
        def __init__(self):
            self.calls = []

        def register_server_function(self, name, *args, **kwargs):
            self.calls.append(name)

    service = Recorder()
    try:
        register_server_functions_from_dir(service, str(base_dir), names=["beta"])
    finally:
        _clear_modules(package_name)
    assert service.calls == ["beta"]
