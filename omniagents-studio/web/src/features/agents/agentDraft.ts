import type { JsonValue } from '../../types'
import type { AgentSource } from '../../types/evaluation'

type Settings = Record<string, JsonValue>

function list(value: JsonValue | undefined): string[] {
  return Array.isArray(value) ? value.map((v) => (typeof v === 'string' ? v : String((v as Settings)?.name ?? ''))) : []
}

function text(value: JsonValue | undefined): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

function json(value: JsonValue | undefined): string {
  return value === undefined || value === null ? '' : JSON.stringify(value, null, 2)
}

/** The form's view of an agent's settings, and turning it back into changes. */
export interface Draft {
  label: string
  description: string
  model: string
  max_turns: string
  instructions: string
  capabilities: string[]
  tools: string[]
  use_safe_agent: boolean
  safe_tool_names: string[]
  halt_on_rejection: boolean
  input_guardrails: string[]
  output_guardrails: string[]
  model_settings: string
  output_schema: string
  output_type_strict: boolean
}

export function draftOf(source: AgentSource): Draft {
  const settings = source.settings ?? {}
  const safe = (settings.safe_agent_options ?? {}) as Settings
  return {
    label: text(settings.label),
    description: text(settings.description),
    model: text(settings.model),
    max_turns: text(settings.max_turns),
    instructions: source.instructions,
    capabilities: list(settings.capabilities),
    tools: list(settings.tools),
    use_safe_agent: settings.use_safe_agent === true,
    safe_tool_names: list(safe.safe_tool_names),
    halt_on_rejection: safe.halt_on_rejection !== false,
    input_guardrails: list(settings.input_guardrails),
    output_guardrails: list(settings.output_guardrails),
    model_settings: json(settings.model_settings),
    output_schema: json(settings.output_schema),
    output_type_strict: settings.output_type_strict !== false,
  }
}

/** The settings that differ from the file, as `PATCH /api/agents/<key>` changes. */
export function changesOf(draft: Draft, original: Draft, settings: Settings): { changes: Settings; problems: string[] } {
  const changes: Settings = {}
  const problems: string[] = []
  const scalar = (key: keyof Draft & string, value: JsonValue | null) => {
    if (draft[key] !== original[key]) changes[key] = value
  }
  scalar('label', draft.label.trim() || null)
  scalar('description', draft.description.trim() || null)
  scalar('model', draft.model.trim() || null)
  scalar('max_turns', draft.max_turns.trim() ? Number(draft.max_turns) : null)
  for (const key of ['capabilities', 'tools', 'input_guardrails', 'output_guardrails'] as const) {
    if (JSON.stringify(draft[key]) !== JSON.stringify(original[key])) changes[key] = draft[key].length ? draft[key] : null
  }
  if (draft.use_safe_agent !== original.use_safe_agent) changes.use_safe_agent = draft.use_safe_agent
  if (
    JSON.stringify(draft.safe_tool_names) !== JSON.stringify(original.safe_tool_names) ||
    draft.halt_on_rejection !== original.halt_on_rejection
  ) {
    changes.safe_agent_options = {
      ...((settings.safe_agent_options as Settings) ?? {}),
      safe_tool_names: draft.safe_tool_names,
      halt_on_rejection: draft.halt_on_rejection,
    }
  }
  for (const key of ['model_settings', 'output_schema'] as const) {
    if (draft[key] === original[key]) continue
    if (!draft[key].trim()) {
      changes[key] = null
      continue
    }
    try {
      changes[key] = JSON.parse(draft[key]) as JsonValue
    } catch {
      problems.push(`${key === 'model_settings' ? 'Model settings' : 'The output schema'} must be JSON`)
    }
  }
  if (draft.output_type_strict !== original.output_type_strict) changes.output_type_strict = draft.output_type_strict
  return { changes, problems }
}
