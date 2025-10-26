import sys
import textwrap
import uuid

from agents.guardrail import InputGuardrail, OutputGuardrail

from omniagents.core.guardrails.discovery import discover_guardrails_in_dir


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
        for mod in list(sys.modules):
            if mod.startswith(package_name):
                sys.modules.pop(mod, None)

    assert set(input_guardrails) == {"shared"}
    assert isinstance(input_guardrails["shared"], InputGuardrail)
    assert set(output_guardrails) == {"output-one"}
    assert isinstance(output_guardrails["output-one"], OutputGuardrail)

    captured = capsys.readouterr()
    assert "Warning: Duplicate input guardrail name 'shared'" in captured.out
