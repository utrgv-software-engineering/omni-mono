import os
from pathlib import Path
import sys


def main() -> None:
    from omniagents.cli import main as omni_main
    from mcp_agent.config import load_config

    for key, value in load_config().items():
        if key not in os.environ:
            os.environ[key] = value

    project_path = Path(__file__).resolve().parent.parent / "project.yml"
    new_args = ["omniagents", "run", "--project", str(project_path)]
    if len(sys.argv) > 1:
        new_args.extend(sys.argv[1:])
    sys.argv = new_args
    omni_main()


if __name__ == "__main__":
    main()
