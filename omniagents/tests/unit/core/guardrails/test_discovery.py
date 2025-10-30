import sys
import textwrap
import uuid

from agents.guardrail import InputGuardrail, OutputGuardrail

from omniagents.core.debug import Debug
from omniagents.core.guardrails.discovery import discover_guardrails_in_dir


def _clear_modules(prefix):
    for mod in list(sys.modules):
        if mod.startswith(prefix):
            sys.modules.pop(mod, None)


def test_discover_guardrails_in_dir_finds_and_warns_duplicates(tmp_path, capsys):
    package_name = f"guard_pkg_{uuid.uuid4().hex}"
    base_dir = tmp_path / package_name
    base_dir.mkdir()
    (base_dir / "__init__.py").write_text("")
    (base_dir / "alpha.py").write_text(
        textwrap.dedent(
            """
            from agents.guardrail import InputGuardrail, OutputGuardrail


            def allow(context, agent, payload):
                return {"allowed": True}


            async def audit(context, agent, payload):
                return {"allowed": True}


            input_guard = InputGuardrail(allow, name="shared")
            output_guard = OutputGuardrail(audit, name="output-one")
            """
        )
    )
    (base_dir / "beta.py").write_text(
        textwrap.dedent(
            """
            from agents.guardrail import InputGuardrail


            def deny(context, agent, payload):
                return {"allowed": False}


            duplicate_guard = InputGuardrail(deny, name="shared")
            """
        )
    )
    try:
        input_guardrails, output_guardrails = discover_guardrails_in_dir(str(base_dir))
    finally:
        _clear_modules(package_name)

    assert set(input_guardrails) == {"shared"}
    assert isinstance(input_guardrails["shared"], InputGuardrail)
    assert set(output_guardrails) == {"output-one"}
    assert isinstance(output_guardrails["output-one"], OutputGuardrail)

    captured = capsys.readouterr()
    assert "Warning: Duplicate input guardrail name 'shared'" in captured.out


def test_discover_guardrails_in_dir_missing_directory_emits_warning(tmp_path, capsys):
    previous = Debug.enabled()
    Debug.set_enabled(True)
    try:
        input_guardrails, output_guardrails = discover_guardrails_in_dir(
            str(tmp_path / "missing")
        )
    finally:
        Debug.set_enabled(previous)
    assert input_guardrails == {}
    assert output_guardrails == {}
    captured = capsys.readouterr()
    assert "Guardrail discovery directory" in captured.out


def test_discover_guardrails_in_dir_restores_sys_path(tmp_path):
    package_name = f"guard_pkg_{uuid.uuid4().hex}"
    base_dir = tmp_path / package_name
    base_dir.mkdir()
    (base_dir / "__init__.py").write_text("")
    (base_dir / "items.py").write_text(
        textwrap.dedent(
            """
            from agents.guardrail import InputGuardrail


            def allow(context, agent, payload):
                return payload


            guard = InputGuardrail(allow, name="sample")
            """
        )
    )
    original_sys_path = list(sys.path)
    try:
        discover_guardrails_in_dir(str(base_dir))
    finally:
        _clear_modules(package_name)
    assert sys.path == original_sys_path


def test_discover_guardrails_in_dir_handles_import_errors(tmp_path, capsys):
    package_name = f"guard_pkg_{uuid.uuid4().hex}"
    base_dir = tmp_path / package_name
    base_dir.mkdir()
    (base_dir / "__init__.py").write_text("")
    (base_dir / "broken.py").write_text("import missing_module")
    (base_dir / "valid.py").write_text(
        textwrap.dedent(
            """
            from agents.guardrail import InputGuardrail


            def allow(context, agent, payload):
                return payload


            guard = InputGuardrail(allow, name="ok")
            """
        )
    )
    try:
        input_guardrails, output_guardrails = discover_guardrails_in_dir(str(base_dir))
    finally:
        _clear_modules(package_name)
    assert set(input_guardrails) == {"ok"}
    assert output_guardrails == {}
    captured = capsys.readouterr()
    assert "Could not import module" in captured.out
