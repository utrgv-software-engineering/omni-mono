import flet as ft
import sys
import argparse
from dotenv import load_dotenv

# --- Core Framework Imports --- #
from omniagents.core.app_base import AgentApp
# Note: All OpenAI client configuration (API key, base URL, tracing) should be done by the user script or environment.
from omniagents.core.config_loader import load_agent_spec_from_yaml

def main():
    """Entry point for the omniagents CLI."""
    try:
        load_dotenv()
    except Exception as e:
        print(f"Warning: Could not load .env file: {e}")

    # --- Command Line Arguments ---
    parser = argparse.ArgumentParser(description="OmniAgents CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run an agent")
    run_parser.add_argument(
        "--mode", "-m",
        choices=["gui", "cli"],
        default="gui",
        help="Run mode: 'gui' (default) for graphical UI, 'cli' for command-line interface"
    )
    run_parser.add_argument(
        "--config",
        "-c",
        type=str,
        required=True,
        help="Path to the agent configuration YAML file."
    )
    run_parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug mode (overrides YAML setting)."
    )
    
    # Save command
    save_parser = subparsers.add_parser("save", help="Save an agent spec as a standalone Python module")
    save_parser.add_argument(
        "--config",
        "-c",
        type=str,
        required=True,
        help="Path to the agent configuration YAML file."
    )
    save_parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="generated_agent.py",
        help="Output Python file path."
    )
    
    args = parser.parse_args()
    
    if args.command == "run":
        CONFIG_FILE_PATH = args.config
        mode = args.mode or "gui"
        if mode == "cli":
            # Run in CLI mode
            from omniagents.cli_runner import run_cli
            try:
                import asyncio
                asyncio.run(run_cli(config_path=CONFIG_FILE_PATH, args=args))
            except KeyboardInterrupt:
                print("\nExiting CLI mode.")
            return

        # Default GUI mode
        
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
    elif args.command == "save":
        try:
            save_agent_as_py(args.config, args.output)
            print(f"Standalone Python module created at: {args.output}")
        except Exception as e:
            print(f"Error saving agent spec: {e}")
            import traceback; traceback.print_exc()
            sys.exit(1)
    else:
        parser.print_help()

def save_agent_as_py(config_path: str, output_path: str):
    """Generate a standalone Python script by inlining all tools and instructions."""
    import yaml
    from pathlib import Path

    # Load the AgentSpec so we can inline spec values
    spec = load_agent_spec_from_yaml(config_path)
    # Capture the list of tool functions for embedding and spec construction
    tool_funcs = spec.available_tools

    cfg_path = Path(config_path)
    raw_cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))

    # Resolve instructions text
    instructions = ''
    if raw_cfg.get('instructions'):
        instructions = raw_cfg['instructions']
    elif raw_cfg.get('instructions_file'):
        instr_file = cfg_path.parent / raw_cfg['instructions_file']
        if not instr_file.is_file():
            alt = Path(raw_cfg['instructions_file'])
            if alt.is_file():
                instr_file = alt
        if instr_file.is_file():
            instructions = instr_file.read_text(encoding='utf-8')
    instructions = instructions.replace("'''", "\\'\\'\\'")

    # Gather tool directories from config
    tool_dirs = []
    base = cfg_path.parent
    default_tools = base / 'tools'
    if default_tools.is_dir():
        tool_dirs.append(default_tools)
    for td in raw_cfg.get('additional_tools_dirs', []):
        p = (base / td).resolve()
        if p.is_dir():
            tool_dirs.append(p)

    # Collect all .py files from those directories
    tool_files = []
    for d in tool_dirs:
        for py in d.rglob('*.py'):
            if py.name != '__init__.py':
                tool_files.append(py)

    # Begin assembling the generated script
    lines: list[str] = []
    lines.append('# Autogenerated standalone agent script\n')

    # --- Embed tool modules ---
    for tf in tool_files:
        rel = tf.relative_to(Path.cwd())
        lines.append(f"# --- From {rel} ---")
        lines.append(tf.read_text(encoding='utf-8'))
        lines.append("\n")

    # --- Embed agent instructions ---
    lines.append("def get_agent_instructions():")
    lines.append("    return '''")
    lines.append(instructions)
    lines.append("'''\n")

    # Imports for building AgentSpec
    lines.append("import flet as ft")
    lines.append("from omniagents.core.app_base import AgentApp")
    lines.append("from omniagents.core.specs import AgentSpec, SafeAgentOptions")
    lines.append("from omniagents.core.settings import SettingField\n")

    # Construct the AgentSpec inline
    lines.append("spec = AgentSpec(")
    lines.append(f"    name={spec.name!r},")
    lines.append(f"    header_title={spec.header_title!r},")
    lines.append(f"    page_title={spec.page_title!r},")
    lines.append(f"    welcome_text={spec.welcome_text!r},")
    lines.append(f"    client_storage_prefix={spec.client_storage_prefix!r},")
    lines.append("    get_agent_instructions=get_agent_instructions,")
    lines.append("    build_agent=None,  # use default internal builder\n")

    # List available tools
    tool_names = [t.name for t in tool_funcs]
    lines.append("    available_tools=[%s]," % ", ".join(tool_names))

    lines.append(f"    model_name={spec.model_name!r},")
    lines.append(f"    use_safe_agent={spec.use_safe_agent},")
    so = spec.safe_agent_options
    lines.append(f"    safe_agent_options=SafeAgentOptions(skip_approvals={so.skip_approvals}, halt_on_rejection={so.halt_on_rejection}, safe_tool_names={so.safe_tool_names!r}, safe_tool_patterns={so.safe_tool_patterns!r}),")
    lines.append(f"    uses_mcp={spec.uses_mcp},")
    lines.append(f"    mcp_url_setting_key={spec.mcp_url_setting_key!r},")
    lines.append(f"    mcp_token_setting_key={spec.mcp_token_setting_key!r},")
    lines.append(f"    show_mcp_status_indicator={spec.show_mcp_status_indicator},")

    # Embed icon_map
    lines.append("    icon_map={")
    for k, v in raw_cfg.get('icon_map', {}).items():
        lines.append(f"        {k!r}: {v},")
    lines.append("    },")

    # Embed settings_fields
    lines.append("    settings_fields=[")
    for field in spec.settings_fields:
        lines.append(f"        SettingField(key={field.key!r}, label={field.label!r}, group={field.group!r}, default={field.default!r}, secret={field.secret}, hint={field.hint!r}),")
    lines.append("    ],")

    lines.append(f"    debug_flag={spec.debug_flag},")
    lines.append(f"    page_title_suffix={spec.page_title_suffix!r},")

    # Theme color
    if 'theme_color' in raw_cfg:
        lines.append(f"    theme_color={raw_cfg['theme_color']},")
    else:
        lines.append(f"    theme_color={spec.theme_color!r},")

    lines.append(")\n")

    # Kick off the Flet app
    lines.append("if __name__ == '__main__':")
    lines.append("    app = AgentApp(spec=spec)")
    lines.append("    ft.app(target=app.main)\n")

    # Write out the file
    Path(output_path).write_text("\n".join(lines), encoding='utf-8')

if __name__ == "__main__":
    main() 