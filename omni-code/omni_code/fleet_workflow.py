from dataclasses import dataclass, field


@dataclass(frozen=True)
class FleetWorkflowColumnDef:
    id: str
    label: str
    checklist: list[str] = field(default_factory=list)
    gate: bool = False


@dataclass(frozen=True)
class FleetWorkflowSupervisorConfig:
    max_concurrent: int | None = None
    stall_timeout_ms: int | None = None
    max_retry_attempts: int | None = None
    max_continuation_turns: int | None = None
    auto_dispatch: bool | None = None
    max_concurrent_by_column: dict[str, int] = field(default_factory=dict)
    continuation_prompt: str | None = None


@dataclass(frozen=True)
class FleetWorkflowHooks:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_ms: int | None = None


@dataclass(frozen=True)
class FleetWorkflowConfig:
    supervisor: FleetWorkflowSupervisorConfig | None = None
    pipeline: list[FleetWorkflowColumnDef] = field(default_factory=list)
    hooks: FleetWorkflowHooks | None = None


@dataclass(frozen=True)
class FleetWorkflow:
    config: FleetWorkflowConfig
    prompt_template: str


def parse_fleet_workflow(content: str) -> FleetWorkflow:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return FleetWorkflow(config=FleetWorkflowConfig(), prompt_template=content.strip())

    frontmatter_end = -1
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            frontmatter_end = index
            break
    if frontmatter_end == -1:
        return FleetWorkflow(config=FleetWorkflowConfig(), prompt_template=content.strip())

    frontmatter = lines[1:frontmatter_end]
    prompt_template = "\n".join(lines[frontmatter_end + 1 :]).strip()

    supervisor: dict[str, object] = {}
    hooks: dict[str, object] = {}
    pipeline: list[FleetWorkflowColumnDef] = []
    current_section: str | None = None
    collecting_multiline: tuple[str, str, list[str]] | None = None
    pipeline_lines: list[str] | None = None

    def apply_value(section: str, key: str, value: str) -> None:
        if section == "supervisor":
            numeric = int(value) if value.isdigit() else None
            if key == "max_concurrent" and numeric and numeric > 0:
                supervisor[key] = numeric
            elif key == "stall_timeout_ms" and numeric and numeric > 0:
                supervisor[key] = numeric
            elif key == "max_retry_attempts" and numeric and numeric > 0:
                supervisor[key] = numeric
            elif key == "max_continuation_turns" and numeric and numeric > 0:
                supervisor[key] = numeric
            elif key == "auto_dispatch":
                supervisor[key] = value == "true"
            elif key == "continuation_prompt" and value:
                supervisor[key] = value
        elif section == "max_concurrent_by_column":
            numeric = int(value) if value.isdigit() else None
            if numeric and numeric > 0:
                current = dict(supervisor.get("max_concurrent_by_column") or {})
                current[key] = numeric
                supervisor["max_concurrent_by_column"] = current
        elif section == "hooks":
            if key == "timeout_ms":
                numeric = int(value) if value.isdigit() else None
                if numeric and numeric > 0:
                    hooks[key] = numeric
            elif value:
                hooks[key] = value

    def parse_pipeline(lines_: list[str]) -> list[FleetWorkflowColumnDef]:
        columns: list[FleetWorkflowColumnDef] = []
        current: dict[str, object] | None = None
        collecting_checklist = False

        def flush() -> None:
            nonlocal current, collecting_checklist
            if current and current.get("id") and current.get("label"):
                columns.append(
                    FleetWorkflowColumnDef(
                        id=str(current["id"]),
                        label=str(current["label"]),
                        checklist=list(current.get("checklist") or []),
                        gate=bool(current.get("gate") or False),
                    )
                )
            current = None
            collecting_checklist = False

        for line in lines_:
            if not line.strip():
                continue
            if line.startswith("  columns:"):
                continue
            if line.startswith("    - "):
                flush()
                key, _, value = line[6:].partition(":")
                current = {key.strip(): value.strip()}
                continue
            if line.startswith("      ") and current is not None:
                body = line[6:]
                if body.startswith("- ") and collecting_checklist:
                    checklist = list(current.get("checklist") or [])
                    checklist.append(body[2:].strip())
                    current["checklist"] = checklist
                    continue
                key, _, value = body.partition(":")
                key = key.strip()
                value = value.strip()
                if key == "checklist" and not value:
                    collecting_checklist = True
                elif key in {"id", "label"}:
                    current[key] = value
                elif key == "gate":
                    current[key] = value == "true"
        flush()
        return columns

    for line in frontmatter:
        if pipeline_lines is not None:
            if line and not line.startswith(" "):
                pipeline = parse_pipeline(pipeline_lines)
                pipeline_lines = None
            else:
                pipeline_lines.append(line)
                continue
        if collecting_multiline is not None:
            section, key, collected = collecting_multiline
            if line.startswith("    ") or (not line.strip() and collected):
                collected.append(line[4:] if line.startswith("    ") else "")
                continue
            apply_value(section, key, "\n".join(collected).strip())
            collecting_multiline = None
        if not line.strip():
            continue
        if line.endswith(":") and not line.startswith(" "):
            current_section = line[:-1].strip()
            if current_section == "pipeline":
                pipeline_lines = []
            continue
        if current_section and line.startswith("  "):
            key, _, raw_value = line[2:].partition(":")
            key = key.strip()
            value = raw_value.strip()
            if value == "|":
                collecting_multiline = (current_section, key, [])
            else:
                apply_value(current_section, key, value)

    if collecting_multiline is not None:
        section, key, collected = collecting_multiline
        apply_value(section, key, "\n".join(collected).strip())
    if pipeline_lines is not None:
        pipeline = parse_pipeline(pipeline_lines)

    return FleetWorkflow(
        config=FleetWorkflowConfig(
            supervisor=FleetWorkflowSupervisorConfig(**supervisor) if supervisor else None,
            pipeline=pipeline,
            hooks=FleetWorkflowHooks(**hooks) if hooks else None,
        ),
        prompt_template=prompt_template,
    )
