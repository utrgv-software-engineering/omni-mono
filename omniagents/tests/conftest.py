import sys
import types
import pytest
import importlib

# ---------------------------------------------------------------------------
# Stub minimal 'flet' module used by config_loader and others
# ---------------------------------------------------------------------------
ft = types.ModuleType('flet')
class Colors:
    BLUE = '#0000FF'
    BLUE_900 = '#000099'
ft.Colors = Colors
class Icons:
    pass
ft.Icons = Icons
# also stub submodule animation used by app_base
ft.animation = types.ModuleType('flet.animation')
sys.modules.setdefault('flet', ft)
sys.modules.setdefault('flet.animation', ft.animation)

# ---------------------------------------------------------------------------
# Stub minimal 'agents' package used across modules
# ---------------------------------------------------------------------------
agents = types.ModuleType('agents')

class Tool:
    def __init__(self, name, description='', params_json_schema=None, on_invoke_tool=None, strict_json_schema=True):
        self.name = name
        self.description = description
        self.params_json_schema = params_json_schema or {}
        self.on_invoke_tool = on_invoke_tool
        self.strict_json_schema = strict_json_schema

class FunctionTool(Tool):
    pass

tool_mod = types.ModuleType('agents.tool')
tool_mod.Tool = Tool
tool_mod.FunctionTool = FunctionTool
sys.modules.setdefault('agents.tool', tool_mod)

class ModelSettings:
    def __init__(self, **kwargs):
        pass

model_settings_mod = types.ModuleType('agents.model_settings')
model_settings_mod.ModelSettings = ModelSettings
sys.modules.setdefault('agents.model_settings', model_settings_mod)

class Agent:
    def __init__(self, name='', tools=None, instructions='', model='', model_settings=None, mcp_servers=None, **kwargs):
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
        return getattr(item, 'content', '')
    @staticmethod
    def extract_last_text(msg):
        return getattr(msg, 'content', '')

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
sys.modules.setdefault('agents', agents)

# ---------------------------------------------------------------------------
# Stub minimal fastapi_websocket_rpc with behaviour just enough for tests
# ---------------------------------------------------------------------------
fastapi_rpc = types.ModuleType('fastapi_websocket_rpc')
from fastapi import WebSocket, WebSocketDisconnect

class RpcMethodsBase:
    def __init__(self):
        self.channel = None

class _Channel:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
    async def async_call(self, method: str, args: dict | None = None):
        await self.websocket.send_json({'method': method, 'params': args or {}})

class WebsocketRPCEndpoint:
    def __init__(self, methods: RpcMethodsBase):
        self.methods = methods
    def register_route(self, app, path: str, dependencies=None):
        dependencies = dependencies or []
        async def handler(websocket: WebSocket):
            await websocket.accept()
            channel = _Channel(websocket)
            self.methods.channel = channel
            while True:
                try:
                    data = await websocket.receive_json()
                except WebSocketDisconnect:
                    break
                method = data.get('method')
                args = data.get('args') or data.get('params') or {}
                request_id = data.get('id')
                func = getattr(self.methods, method)
                result = await func(**args)
                await websocket.send_json({'id': request_id, 'result': result})
        app.add_api_websocket_route(path, handler, dependencies=dependencies)

fastapi_rpc.RpcMethodsBase = RpcMethodsBase
fastapi_rpc.WebsocketRPCEndpoint = WebsocketRPCEndpoint
sys.modules.setdefault('fastapi_websocket_rpc', fastapi_rpc)

# ---------------------------------------------------------------------------
# Pytest fixture: temporary history DB for tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def temp_history_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'history.db'
    monkeypatch.setenv('OMNIAGENTS_HISTORY_DB', str(db_path))
    # Reload modules that depend on this env var
    import omniagents.core.local_sessions as local_sessions
    import omniagents.server.history_db as history_db
    importlib.reload(local_sessions)
    importlib.reload(history_db)
    yield

