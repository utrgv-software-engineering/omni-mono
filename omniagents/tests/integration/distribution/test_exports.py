import importlib
import sys
import types


def test_package_exports_available():
    import omniagents

    assert isinstance(omniagents.__version__, str)
    assert omniagents.Runner is not None
    assert callable(omniagents.function_tool)
    assert callable(omniagents.server_function)
    assert callable(omniagents.input_guardrail)
    assert callable(omniagents.output_guardrail)
    assert omniagents.GuardrailFunctionOutput is not None


def test_package_exports_missing_dependencies(monkeypatch):
    monkeypatch.delitem(sys.modules, "omniagents", raising=False)

    stub_version = types.ModuleType("omniagents._version")
    stub_core = types.ModuleType("omniagents.core")
    stub_runtime = types.ModuleType("omniagents.core.runtime")
    stub_runner = types.ModuleType("omniagents.core.runtime.runner")
    stub_core.__path__ = []
    stub_runtime.__path__ = []

    monkeypatch.setitem(sys.modules, "omniagents._version", stub_version)
    monkeypatch.setitem(sys.modules, "omniagents.core", stub_core)
    monkeypatch.setitem(sys.modules, "omniagents.core.runtime", stub_runtime)
    monkeypatch.setitem(sys.modules, "omniagents.core.runtime.runner", stub_runner)

    broken = importlib.import_module("omniagents")

    assert broken.__version__ == "0.0.0"
    assert broken.Runner is None
    assert broken.function_tool is None
    assert broken.server_function is None
    assert broken.input_guardrail is None
    assert broken.output_guardrail is None
    assert broken.GuardrailFunctionOutput is None
