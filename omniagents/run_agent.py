import flet as ft
import sys # To read command line args
import argparse
from dotenv import load_dotenv

# --- Core Framework Imports --- #
from omniagents.core import AgentApp
from agents import (
    set_default_openai_api,
    set_tracing_disabled
)

# --- Configuration & Setup ---

try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Could not load .env file: {e}")

# --- Command Line Arguments ---
parser = argparse.ArgumentParser(description="Flet Agent Runner")
parser.add_argument(
    "--config",
    "-c",
    type=str,
    default="agent.yaml",
    help="Path to the agent configuration YAML file."
)
parser.add_argument(
    "--debug",
    "-d",
    action="store_true",
    help="Enable debug mode (overrides YAML setting)."
)
args = parser.parse_args()

CONFIG_FILE_PATH = args.config

# --- Global Settings ---
set_tracing_disabled(disabled=True)
set_default_openai_api("chat_completions")

# --- Main Execution ---

if __name__ == "__main__":
    try:
        # Load AgentSpec from YAML via AgentApp
        print(f"Loading agent configuration from: {CONFIG_FILE_PATH}")
        app = AgentApp(config_path=CONFIG_FILE_PATH)

        # Override Debug Mode from Command Line Argument if provided
        if args.debug:
            print("Overriding debug mode: Setting to True based on command line argument.")
            app.spec.debug_flag = True
            if "(Debug Mode)" not in app.spec.page_title_suffix:
                 app.spec.page_title_suffix += " (Debug Mode)"

        # Run the Flet App
        print(f"Starting Flet app for agent: {app.spec.name}")
        ft.app(
            target=app.main,
            # Other ft.app options can be added here (e.g., assets_dir, port)
        )

    except FileNotFoundError:
        print(f"Error: Configuration file '{CONFIG_FILE_PATH}' not found.")
        print("Please ensure the YAML configuration file exists.")
        sys.exit(1)
    except ImportError as e:
        print(f"Error: Failed to import required modules: {e}")
        if "PyYAML" in str(e):
            print("Hint: PyYAML is required for loading config files. Install it using: pip install pyyaml")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)