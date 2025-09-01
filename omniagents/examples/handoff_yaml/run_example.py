#!/usr/bin/env python3
"""
Example of using handoffs with YAML configuration.

This demonstrates how to create a main assistant that can delegate
to specialized agents defined in separate YAML files.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path to import omniagents
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from omniagents.cli import Runner


def main():
    """Run the handoff example."""
    # Path to the main agent's YAML configuration
    config_path = Path(__file__).parent / "main_agent.yaml"
    
    # Create and run the agent
    runner = Runner(config_path=str(config_path))
    
    # Run in CLI mode by default
    runner.run(mode="cli")


if __name__ == "__main__":
    main()