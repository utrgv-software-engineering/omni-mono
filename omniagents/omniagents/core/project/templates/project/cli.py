from pathlib import Path
import sys

def main() -> None:
    from omniagents.cli import main as omni_main

    project_path = Path(__file__).resolve().parent / "project.yml"
    new_args = ["omniagents", "run", "--project", str(project_path)]
    if len(sys.argv) > 1:
        new_args.extend(sys.argv[1:])
    sys.argv = new_args
    omni_main()

if __name__ == "__main__":
    main()
