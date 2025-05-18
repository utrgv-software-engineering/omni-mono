import os
import tempfile
from omniagents.core.config_loader import load_agent_spec_from_yaml


def test_load_agent_spec_from_yaml(tmp_path):
    yaml_path = tmp_path / 'agent.yaml'
    instr_path = tmp_path / 'instr.txt'
    instr_path.write_text('Do things')
    yaml_path.write_text(
        'name: TestAgent\ninstructions_file: instr.txt\nmax_turns: 7\nwelcome_text: hi\n'
    )

    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.name == 'TestAgent'
    assert spec.max_turns == 7
    assert spec.welcome_text == 'hi'
    assert spec.get_agent_instructions() == 'Do things'
    # default prefix should come from name
    assert spec.client_storage_prefix.startswith('testagent')
