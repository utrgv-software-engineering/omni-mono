import pytest

from omniagents.core.agents.specs import HandoffConfig


def test_handoff_config_requires_single_source_inline():
    with pytest.raises(ValueError):
        HandoffConfig()


def test_handoff_config_requires_single_source_both():
    with pytest.raises(ValueError):
        HandoffConfig(yaml_path="a.yaml", inline_spec=object())
