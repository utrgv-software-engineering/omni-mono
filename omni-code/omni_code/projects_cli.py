import argparse
import json
import sys
import time

from omni_code.projects_runtime import ProjectsRuntime, get_fleet_store_path
from omni_code.tui_launcher import launch_projects_tui
from omni_code.work_cli import cmd_artifact_read as work_cmd_artifact_read
from omni_code.work_cli import cmd_artifacts as work_cmd_artifacts
from omni_code.work_cli import cmd_diff as work_cmd_diff
from omni_code.work_cli import cmd_history as work_cmd_history
from omni_code.work_cli import supervisor_send as work_supervisor_send
from omni_code.work_cli import supervisor_start as work_supervisor_start
from omni_code.work_cli import supervisor_auto as work_supervisor_auto
from omni_code.work_cli import supervisor_stop as work_supervisor_stop
from omni_code.work_cli import supervisor_log as work_supervisor_log


def _dump_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _project_payload(project, current_project_id: str | None) -> dict:
    return {
        "id": project.id,
        "label": project.label,
        "workspace_dir": project.workspace_dir,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "is_current": project.id == current_project_id,
    }


def cmd_list(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    projects = runtime.list_projects()
    current = runtime.get_current_project()
    payload = [_project_payload(project, current.id if current else None) for project in projects]
    if args.json:
        print(_dump_json(payload))
        return
    if not payload:
        print("No projects registered.")
        return
    for item in payload:
        prefix = "*" if item["is_current"] else " "
        print(f"{prefix} {item['label']}  {item['workspace_dir']}")


def cmd_add(args: argparse.Namespace) -> None:
    from pathlib import Path

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise RuntimeError(f"Workspace directory not found: {workspace}")
    label = args.label or workspace.name
    runtime = ProjectsRuntime()
    project = runtime.add_project(label=label, workspace_dir=workspace, project_id=args.id)
    print(f"Added project {project.label}")


def cmd_remove(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    project = runtime.remove_project(args.project)
    print(f"Removed project {project.label}")


def cmd_use(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    project = runtime.set_current_project(args.project)
    print(f"Current project: {project.label}")


def cmd_show(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    project = runtime.resolve_project(args.project)
    if project is None:
        raise RuntimeError(f"Project not found: {args.project}")
    current = runtime.get_current_project()
    payload = _project_payload(project, current.id if current else None)
    if args.json:
        print(_dump_json(payload))
        return
    print(f"Label: {project.label}")
    print(f"Workspace: {project.workspace_dir}")
    print(f"Current: {'yes' if payload['is_current'] else 'no'}")


def cmd_tui(args: argparse.Namespace) -> None:
    launch_projects_tui(projects_config_path=get_fleet_store_path(), debug=bool(args.debug))


def cmd_ticket_list(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    tickets = runtime.list_tickets(args.project)
    payload = [
        {
            "id": ticket.id,
            "project_id": ticket.project_id,
            "title": ticket.title,
            "description": ticket.description,
            "priority": ticket.priority,
            "blocked_by": ticket.blocked_by,
            "column_id": ticket.column_id,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
        }
        for ticket in tickets
    ]
    if args.json:
        print(_dump_json(payload))
        return
    if not payload:
        print("No tickets.")
        return
    for ticket in payload:
        print(f"{ticket['id']}  [{ticket['column_id']}]  {ticket['title']}")


def cmd_ticket_add(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    ticket = runtime.add_ticket(
        project_ref=args.project,
        title=args.title,
        description=args.description or "",
        priority=args.priority,
    )
    print(f"Added ticket {ticket.title}")


def cmd_ticket_move(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    ticket = runtime.move_ticket(args.ticket, args.column, project_ref=args.project)
    print(f"Moved ticket {ticket.title} to {ticket.column_id}")


def cmd_ticket_history(args: argparse.Namespace) -> None:
    work_cmd_history(args)


def cmd_ticket_artifacts(args: argparse.Namespace) -> None:
    work_cmd_artifacts(args)


def cmd_ticket_artifact_read(args: argparse.Namespace) -> None:
    work_cmd_artifact_read(args)


def cmd_ticket_diff(args: argparse.Namespace) -> None:
    work_cmd_diff(args)


def cmd_ticket_supervisor_start(args: argparse.Namespace) -> None:
    prompt = " ".join(args.prompt).strip() or "Begin working on this ticket."
    result = work_supervisor_start(args.ticket, prompt, args.project)
    if args.json:
        print(_dump_json(result))
        return
    print(f"Started supervisor for {args.ticket}")
    print(f"Session: {result['session_id']}")
    print(f"Run: {result['run_id']}")


def cmd_ticket_supervisor_send(args: argparse.Namespace) -> None:
    work_supervisor_send(args.ticket, args.message, args.project)
    print(f"Sent message to supervisor for {args.ticket}")


def cmd_ticket_supervisor_stop(args: argparse.Namespace) -> None:
    work_supervisor_stop(args.ticket, args.project)
    print(f"Stopped supervisor for {args.ticket}")


def cmd_ticket_supervisor_auto(args: argparse.Namespace) -> None:
    prompt = " ".join(args.prompt).strip() or "Begin working on this ticket."
    try:
        result = work_supervisor_auto(args.ticket, prompt, args.project)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return
    if args.json:
        print(_dump_json(result))
        return
    print(f"Supervisor auto finished: {result.get('phase', 'unknown')}")
    print(f"  Reason: {result.get('stop_reason', '-')}")
    print(f"  Continuation turns: {result.get('continuation_turns', 0)}")
    print(f"  Retry attempts: {result.get('retry_attempts', 0)}")


def cmd_ticket_supervisor_log(args: argparse.Namespace) -> None:
    work_supervisor_log(args.ticket, args.project)


def cmd_ticket_sync(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    result = runtime.sync_ticket_file(args.ticket, args.project)
    if args.json:
        print(_dump_json(result))
        return
    if result["column_id"]:
        print(f"Synced ticket {args.ticket} to column {result['column_id']}")
    if result["escalation"]:
        print(f"Escalation: {result['escalation']}")
    if not result["column_id"] and not result["escalation"]:
        print("No ticket file changes applied.")


def cmd_ticket_watch(args: argparse.Namespace) -> None:
    runtime = ProjectsRuntime()
    ticket = runtime.resolve_ticket(args.ticket, args.project)
    if ticket is None:
        raise RuntimeError(f"Ticket not found: {args.ticket}")
    ticket_file = runtime._ticket_file_path(ticket.id)
    last_mtime = ticket_file.stat().st_mtime if ticket_file.exists() else None

    def sync_once() -> bool:
        nonlocal last_mtime
        current_mtime = ticket_file.stat().st_mtime if ticket_file.exists() else None
        if current_mtime == last_mtime and not args.force:
            return False
        last_mtime = current_mtime
        result = runtime.sync_ticket_file(ticket.id, ticket.project_id)
        if args.json:
            print(_dump_json(result))
        else:
            if result["column_id"]:
                print(f"Synced ticket {ticket.id} to column {result['column_id']}")
            if result["escalation"]:
                print(f"Escalation: {result['escalation']}")
            if not result["column_id"] and not result["escalation"]:
                print("No ticket file changes applied.")
        return True

    if args.once:
        sync_once()
        return

    while True:
        try:
            sync_once()
            args.force = False
            time.sleep(args.poll_interval)
        except KeyboardInterrupt:
            return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omni projects", description="Manage Omni work projects")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List registered projects")
    list_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    list_parser.set_defaults(func=cmd_list)

    add_parser = subparsers.add_parser("add", help="Register a workspace as a project")
    add_parser.add_argument("workspace")
    add_parser.add_argument("--label", default=None)
    add_parser.add_argument("--id", default=None)
    add_parser.set_defaults(func=cmd_add)

    remove_parser = subparsers.add_parser("remove", help="Remove a project")
    remove_parser.add_argument("project")
    remove_parser.set_defaults(func=cmd_remove)

    use_parser = subparsers.add_parser("use", help="Set the current project")
    use_parser.add_argument("project")
    use_parser.set_defaults(func=cmd_use)

    show_parser = subparsers.add_parser("show", help="Show one project")
    show_parser.add_argument("project")
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    show_parser.set_defaults(func=cmd_show)

    tui_parser = subparsers.add_parser("tui", help="Open the projects TUI")
    tui_parser.add_argument("--debug", action="store_true", help="Enable TUI debug logging")
    tui_parser.set_defaults(func=cmd_tui)

    ticket_parser = subparsers.add_parser("ticket", help="Manage project tickets")
    ticket_subparsers = ticket_parser.add_subparsers(dest="ticket_command")

    ticket_list = ticket_subparsers.add_parser("list", help="List tickets")
    ticket_list.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    ticket_list.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ticket_list.set_defaults(func=cmd_ticket_list)

    ticket_add = ticket_subparsers.add_parser("add", help="Add a ticket")
    ticket_add.add_argument("--project", required=True, help="Project label, ID, or workspace path")
    ticket_add.add_argument("title")
    ticket_add.add_argument("--description", default="")
    ticket_add.add_argument("--priority", default="medium", choices=["low", "medium", "high", "critical"])
    ticket_add.set_defaults(func=cmd_ticket_add)

    ticket_move = ticket_subparsers.add_parser("move", help="Move a ticket to a pipeline column")
    ticket_move.add_argument("ticket")
    ticket_move.add_argument("column")
    ticket_move.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    ticket_move.set_defaults(func=cmd_ticket_move)

    ticket_history = ticket_subparsers.add_parser("history", help="Show session history for a ticket")
    ticket_history.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    ticket_history.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    ticket_history.add_argument("--limit", type=int, default=None, help="Show only the most recent N messages")
    ticket_history.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ticket_history.set_defaults(func=cmd_ticket_history)

    ticket_artifacts = ticket_subparsers.add_parser("artifacts", help="List ticket artifacts")
    ticket_artifacts.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    ticket_artifacts.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    ticket_artifacts.add_argument("--dir", default=None, help="Subdirectory under the ticket artifacts root")
    ticket_artifacts.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ticket_artifacts.set_defaults(func=cmd_ticket_artifacts)

    ticket_artifact_read = ticket_subparsers.add_parser("artifact-read", help="Read a text artifact for a ticket")
    ticket_artifact_read.add_argument("path", help="Relative path under the ticket artifacts root")
    ticket_artifact_read.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    ticket_artifact_read.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    ticket_artifact_read.set_defaults(func=cmd_ticket_artifact_read)

    ticket_diff = ticket_subparsers.add_parser("diff", help="Show git status for a ticket workspace")
    ticket_diff.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    ticket_diff.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    ticket_diff.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ticket_diff.set_defaults(func=cmd_ticket_diff)

    supervisor_start = ticket_subparsers.add_parser("supervisor-start", help="Start a supervisor run for a ticket")
    supervisor_start.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    supervisor_start.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    supervisor_start.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    supervisor_start.add_argument("prompt", nargs="*", help="Optional prompt override")
    supervisor_start.set_defaults(func=cmd_ticket_supervisor_start)

    supervisor_send = ticket_subparsers.add_parser("supervisor-send", help="Send a message to an active supervisor run")
    supervisor_send.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    supervisor_send.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    supervisor_send.add_argument("message")
    supervisor_send.set_defaults(func=cmd_ticket_supervisor_send)

    supervisor_stop = ticket_subparsers.add_parser("supervisor-stop", help="Stop an active supervisor run")
    supervisor_stop.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    supervisor_stop.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    supervisor_stop.set_defaults(func=cmd_ticket_supervisor_stop)

    supervisor_auto = ticket_subparsers.add_parser("supervisor-auto", help="Run a supervisor in auto mode (continues/retries until complete)")
    supervisor_auto.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    supervisor_auto.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    supervisor_auto.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    supervisor_auto.add_argument("prompt", nargs="*", help="Optional prompt override")
    supervisor_auto.set_defaults(func=cmd_ticket_supervisor_auto)

    supervisor_log = ticket_subparsers.add_parser("supervisor-log", help="Stream live supervisor output (read-only)")
    supervisor_log.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    supervisor_log.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    supervisor_log.set_defaults(func=cmd_ticket_supervisor_log)

    ticket_sync = ticket_subparsers.add_parser("sync", help="Sync inbound TICKET.yaml changes into Fleet state")
    ticket_sync.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    ticket_sync.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    ticket_sync.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ticket_sync.set_defaults(func=cmd_ticket_sync)

    ticket_watch = ticket_subparsers.add_parser("watch", help="Watch TICKET.yaml for inbound changes and sync them")
    ticket_watch.add_argument("--ticket", required=True, help="Fleet ticket ID or title")
    ticket_watch.add_argument("--project", default=None, help="Project label, ID, or workspace path")
    ticket_watch.add_argument("--once", action="store_true", help="Sync once and exit")
    ticket_watch.add_argument("--force", action="store_true", help="Force a sync even if the file mtime has not changed")
    ticket_watch.add_argument("--poll-interval", type=float, default=1.0, help="Polling interval in seconds")
    ticket_watch.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ticket_watch.set_defaults(func=cmd_ticket_watch)

    parser.add_argument("--debug", action="store_true", help="Enable TUI debug logging when launching without a subcommand")

    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "command", None) is None:
        cmd_tui(args)
        return

    try:
        args.func(args)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)
