import sys
import types
import pytest
import importlib

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

# ---------------------------------------------------------------------------
# Stub minimal 'agents' package used across modules
# ---------------------------------------------------------------------------
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
        pass


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
sys.modules.setdefault("agents", agents)


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
