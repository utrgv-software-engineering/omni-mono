export type JsonPrimitive = string | number | boolean | null
export type JsonObject = { [key: string]: JsonValue }
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[]

export function isJsonObject(value: unknown): value is JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }
  return Object.values(value).every(isJsonValue)
}

export function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') {
    return true
  }
  if (typeof value === 'number') {
    return Number.isFinite(value)
  }
  if (Array.isArray(value)) {
    return value.every(isJsonValue)
  }
  return isJsonObject(value)
}

export interface SpanUsage {
  input?: number
  output?: number
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
}

export type SpanValue = JsonValue | SpanRecord[]

export interface SpanRecord {
  arguments?: JsonValue
  call_id?: string
  content?: string | SpanRecord[]
  description?: string
  from_agent?: string
  function?: SpanRecord
  handoffs?: string[]
  id?: string
  input?: SpanValue
  instructions?: string | SpanRecord[]
  name?: string
  output?: SpanValue
  output_type?: string
  response?: SpanRecord
  role?: string
  text?: string
  to_agent?: string
  tool_call_id?: string
  tool_calls?: SpanRecord[]
  tools?: string[]
  triggered?: boolean
  type?: string
  usage?: SpanUsage
}

export interface SpanPayload {
  span_data?: SpanRecord
}

export interface SyntheticDimensionValue {
  name?: string
  label?: string
  description?: string
  examples?: string[]
}

export interface SyntheticDimension {
  name?: string
  label?: string
  description?: string
  values?: SyntheticDimensionValue[]
}

export interface SyntheticMetadata {
  catalog?: { dimensions?: SyntheticDimension[] }
  selection?: { mapping?: Record<string, string> }
}

export interface OmniagentsTraceMetadata {
  run_type?: string
  synthetic?: SyntheticMetadata
  expected?: JsonObject
}

export interface TraceMetadata {
  input?: JsonValue
  user_input?: JsonValue
  prompt?: JsonValue
  message?: JsonValue
  query?: JsonValue
  output?: JsonValue
  response?: JsonValue
  result?: JsonValue
  completion?: JsonValue
  answer?: JsonValue
  omniagents?: OmniagentsTraceMetadata
}

export interface Trace {
  trace_id: string
  workflow_name: string | null
  group_id: string | null
  project: string
  metadata: TraceMetadata
  created_at: string
  duration?: number
  has_errors?: boolean
}

export interface Span {
  span_id: string
  trace_id: string
  parent_id: string | null
  span_type: SpanType
  span_name: string | null
  data: SpanPayload
  started_at: string | null
  ended_at: string | null
  error: Record<string, JsonValue> | null
  sequence_number: number
  created_at: string
  // Computed fields
  duration?: number
  children?: Span[]
  depth?: number
}

export type SpanType = 
  | 'agent' 
  | 'function' 
  | 'generation' 
  | 'handoff' 
  | 'guardrail' 
  | 'response' 
  | 'custom' 
  | 'transcription' 
  | 'speech' 
  | 'speech-group' 
  | 'mcp_tools'

export interface TraceWithSpans {
  trace: Trace
  spans: Span[]
}

/** A failure category (`evaluation.failure_categories`): what a scenario
 * probes and a reviewer can mark a conversation with. */
export interface FailureCategory {
  id: string
  name: string
  description: string | null
  measures: string[]
  /** Scenarios labelled with it. */
  scenario_count: number
  /** Conversations reviewers marked with it. */
  review_count: number
}

export interface AnalysisData {
  markedForAnalysis: boolean
  judgment: 'acceptable' | 'unacceptable' | null
  notes: string
  analyzedAt?: string
  categories?: string[]
}

export interface AgentSummary {
  key: string
  label: string
  description: string
  is_entry: boolean
  /** Why the agent does not load, when it does not. */
  error: string | null
}

/** What a run of an agent is given, as its runtime resolves it
 * (`GET /api/agents/<key>`, from `AgentService.describe_agent`). */
export interface AgentDescription {
  name: string
  label: string
  description: string
  model: { name: string | null; settings: Record<string, JsonValue> }
  instructions: { text: string; dynamic: boolean; error: string | null }
  tools: Array<{
    name: string
    description: string
    kind: string
    /** `always` asks first; `conditional` decides per call; `never` runs. */
    approval: 'always' | 'conditional' | 'never'
    parameters: Record<string, JsonValue> | null
  }>
  /** The checks a run applies, by where they apply. */
  guardrails: { input: string[]; output: string[]; tool_input: string[]; tool_output: string[] }
  /** The structured output a run must end with, or null for text. */
  output: { name: string; strict: boolean; schema: Record<string, JsonValue> } | null
  handoffs: Array<{ name: string; tool: string | null; description: string }>
  agent_tools: Array<{ name: string; agent: string; description: string; max_turns: number | null }>
  capabilities: string[]
  mcp_servers: Array<{ name: string; type: string }>
  skills: Array<{ name: string; description: string; error?: string }>
  server_functions: Array<{ name: string; description: string }>
  policy: {
    approvals: 'enforced' | 'safe_agent' | 'none'
    skip_approvals: boolean
    safe_tool_names: string[]
    safe_tool_patterns: string[]
    max_turns: number | null
    compaction: Record<string, JsonValue> | null
  }
}

export interface StudioProjectMetadata {
  available: boolean
  project?: string | null
  manifest_path?: string | null
}
