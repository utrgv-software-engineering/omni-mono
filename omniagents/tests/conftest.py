import gc
import importlib
import sys
import types

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "filterwarnings",
        "ignore:unclosed database:ResourceWarning",
    )


# ---------------------------------------------------------------------------
# Stub minimal 'flet' module used by config_loader and others
# ---------------------------------------------------------------------------
ft = types.ModuleType("flet")


class Colors:
    BLUE = "#0000FF"
    BLUE_900 = "#000099"
    WHITE = "#FFFFFF"
    BLACK = "#000000"
    BLACK12 = "#000000"
    BLACK45 = "#000000"
    BLACK54 = "#000000"
    BLUE_200 = "#0000DD"
    INDIGO_900 = "#0000DD"
    ORANGE = "#FFA500"

    @staticmethod
    def with_opacity(opacity, color):
        return color


ft.Colors = Colors


class Icons:
    BUILD = "BUILD"


ft.Icons = Icons


# Basic component placeholder used for most UI classes
class _Component:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Component()

    def update(self, *args, **kwargs):
        pass


# Common UI classes used across modules
ft.Container = _Component
ft.Row = _Component
ft.Column = _Component
ft.Text = _Component
ft.Icon = _Component
ft.View = _Component
ft.Control = _Component
ft.Page = _Component
ft.TextField = _Component
ft.IconButton = _Component
ft.FilledButton = _Component
ft.ListView = _Component
ft.ButtonStyle = _Component
ft.SafeArea = _Component
ft.CircleBorder = _Component
ft.BoxShadow = _Component
ft.Offset = _Component
ft.LinearGradient = _Component
ft.Markdown = _Component
ft.MarkdownExtensionSet = _Component
ft.MarkdownCodeTheme = _Component
ft.ThemeMode = types.SimpleNamespace(LIGHT="light")
ft.MainAxisAlignment = types.SimpleNamespace(START=0, END=1, SPACE_BETWEEN=2)
ft.CrossAxisAlignment = types.SimpleNamespace(START=0, END=1, CENTER=2, STRETCH=3)
ft.TextAlign = types.SimpleNamespace(CENTER=0)
ft.alignment = types.SimpleNamespace(
    center=None, center_right=None, top_left=None, bottom_right=None
)
ft.padding = types.SimpleNamespace(
    all=lambda *a, **k: None, symmetric=lambda *a, **k: None, only=lambda *a, **k: None
)
ft.margin = types.SimpleNamespace(
    all=lambda *a, **k: None, symmetric=lambda *a, **k: None, only=lambda *a, **k: None
)
ft.border = types.SimpleNamespace(
    all=lambda *a, **k: None, only=lambda *a, **k: None, BorderSide=_Component
)
ft.icons = types.SimpleNamespace(
    VERIFIED_USER="VERIFIED_USER", ARROW_BACK="ARROW_BACK", SETTINGS="SETTINGS"
)
# also stub submodule animation used by app_base
ft.animation = types.ModuleType("flet.animation")
ft.animation.Animation = _Component
ft.AnimationCurve = types.SimpleNamespace(EASE_OUT=0)
sys.modules.setdefault("flet", ft)
sys.modules.setdefault("flet.animation", ft.animation)

try:
    import agents  # noqa: F401
except ModuleNotFoundError:
    # -----------------------------------------------------------------------
    # Stub minimal 'agents' package used across modules when SDK is missing
    # -----------------------------------------------------------------------
    agents = types.ModuleType("agents")

    class Tool:
        def __init__(
            self,
            name,
            description="",
            params_json_schema=None,
            on_invoke_tool=None,
            strict_json_schema=True,
        ):
            self.name = name
            self.description = description
            self.params_json_schema = params_json_schema or {}
            self.on_invoke_tool = on_invoke_tool
            self.strict_json_schema = strict_json_schema

    class FunctionTool(Tool):
        pass

    tool_mod = types.ModuleType("agents.tool")
    tool_mod.Tool = Tool
    tool_mod.FunctionTool = FunctionTool

    def function_tool(func):
        setattr(func, "_is_omniagents_core_tool", True)
        return func

    tool_mod.function_tool = function_tool
    sys.modules.setdefault("agents.tool", tool_mod)

    guardrail_mod = types.ModuleType("agents.guardrail")

    class _InputGuardrail:
        def __init__(self, *args, **kwargs):
            pass

        def __class_getitem__(cls, item):  # noqa: D401
            return cls

    class _OutputGuardrail:
        def __init__(self, *args, **kwargs):
            pass

        def __class_getitem__(cls, item):  # noqa: D401
            return cls

    guardrail_mod.InputGuardrail = _InputGuardrail
    guardrail_mod.OutputGuardrail = _OutputGuardrail
    sys.modules.setdefault("agents.guardrail", guardrail_mod)

    class ModelSettings:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    model_settings_mod = types.ModuleType("agents.model_settings")
    model_settings_mod.ModelSettings = ModelSettings
    sys.modules.setdefault("agents.model_settings", model_settings_mod)

    strict_schema_mod = types.ModuleType("agents.strict_schema")

    def ensure_strict_json_schema(schema):
        return schema

    strict_schema_mod.ensure_strict_json_schema = ensure_strict_json_schema
    sys.modules.setdefault("agents.strict_schema", strict_schema_mod)

    class Agent:
        def __init__(
            self,
            name="",
            tools=None,
            instructions="",
            model="",
            model_settings=None,
            mcp_servers=None,
            **kwargs,
        ):
            self.name = name
            self.tools = tools or []
            self.instructions = instructions
            self.model = model
            self.model_settings = model_settings
            self.mcp_servers = mcp_servers or []

        async def get_all_tools(self):
            return self.tools

        def reset_run_state(self):
            pass

    class ItemHelpers:
        @staticmethod
        def text_message_output(item):
            return getattr(item, "content", "")

        @staticmethod
        def extract_last_text(msg):
            return getattr(msg, "content", "")

    class Runner:
        @staticmethod
        def run_streamed(agent, messages, max_turns=20, run_config=None):
            class _Result:
                async def stream_events(self):
                    if False:
                        yield None

            return _Result()

    class RunConfig:
        pass

    class StreamEvent:
        def __init__(self, type, item=None):
            self.type = type
            self.item = item

    class RunItemStreamEvent(StreamEvent):
        pass

    agents.Agent = Agent
    agents.ItemHelpers = ItemHelpers
    agents.Runner = Runner
    agents.RunConfig = RunConfig
    agents.StreamEvent = StreamEvent
    agents.RunItemStreamEvent = RunItemStreamEvent
    agents.tool = tool_mod
    agents.model_settings = model_settings_mod

    def set_default_openai_client(*args, **kwargs):
        pass

    def set_default_openai_api(*args, **kwargs):
        pass

    def set_tracing_disabled(*args, **kwargs):
        pass

    agents.set_default_openai_client = set_default_openai_client
    agents.set_default_openai_api = set_default_openai_api
    agents.set_tracing_disabled = set_tracing_disabled

    agent_output_mod = types.ModuleType("agents.agent_output")

    class AgentOutputSchema:
        def __init__(self, output_type, strict_json_schema=True):
            self._output_type = output_type
            self._strict = bool(strict_json_schema)

        def is_plain_text(self):
            return self._output_type in (None, str)

        def json_schema(self):
            mapping = {
                dict: "object",
                list: "array",
                bool: "boolean",
                int: "integer",
                float: "number",
                str: "string",
            }
            type_name = mapping.get(self._output_type, "object")
            return {"type": type_name, "strict": self._strict}

    agent_output_mod.AgentOutputSchema = AgentOutputSchema
    sys.modules.setdefault("agents.agent_output", agent_output_mod)
    sys.modules.setdefault("agents", agents)


# ---------------------------------------------------------------------------
# Pytest fixture: no ambient product discovery
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_ambient_product_discovery(monkeypatch):
    """Framework tests must not inherit a product identity from
    distributions installed in the dev environment (e.g. an editable
    omni-code declaring an ``omniagents.products`` entry point).
    Discovery tests re-enable it explicitly with ``delenv``."""
    monkeypatch.setenv("OMNIAGENTS_PRODUCT_DISCOVERY", "0")


# ---------------------------------------------------------------------------
# Pytest fixture: process-global product identity must not leak across tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_product_identity():
    """``load_project()`` installs a manifest's ``product:`` section as the
    process-wide default identity (``omniagents.core.product``). Without
    per-test restoration, any test that loads a project fixture carrying a
    ``product:`` section (e.g. the studio ``basic_project`` fixture, slug
    ``studio_fixture``) silently rewrites how every later test derives
    slugs, dot-dirs, and config paths — skill roots become
    ``.studio_fixture/skills`` instead of ``.omniagents/skills``. Snapshot
    the identity globals before each test and restore them after, so a
    test only ever sees identity state it installed itself."""
    from omniagents.core import product

    current = product._current
    explicitly_set = product._explicitly_set
    discovered = product._discovered
    yield
    product._current = current
    product._explicitly_set = explicitly_set
    product._discovered = discovered


# ---------------------------------------------------------------------------
# Pytest fixture: temporary history DB for tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def temp_history_db(tmp_path, monkeypatch):
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(db_path))
    # Reload modules that depend on this env var
    import omniagents.core.session.history_db as history_db
    import omniagents.core.session.manager as manager

    importlib.reload(history_db)
    importlib.reload(manager)
    yield
    for connection, _ in history_db._STORES.values():
        connection.close()
    history_db._STORES.clear()
    # The canonical-conversation store caches a sqlite connection per
    # resolved db path; closing it here keeps the per-test temp dir free of
    # open handles (Windows) and the cache from growing across the suite.
    from omniagents.core.conversation.store import reset_conversation_store

    reset_conversation_store()
    gc.collect()
