import pytest

from tests.fixtures.agent_specs import create_server_rpc_agent_spec


@pytest.fixture
def server_rpc_agent_spec():
    return create_server_rpc_agent_spec()


@pytest.fixture
def safe_server_rpc_agent_spec():
    spec = create_server_rpc_agent_spec()
    spec.use_safe_agent = True
    spec.safe_agent_options.safe_tool_names = []
    spec.safe_agent_options.skip_approvals = False
    return spec
