export interface Trace {
  trace_id: string
  workflow_name: string | null
  group_id: string | null
  project: string
  metadata: Record<string, any>
  created_at: string
}

export interface Span {
  span_id: string
  trace_id: string
  parent_id: string | null
  span_type: SpanType
  span_name: string | null
  data: Record<string, any>
  started_at: string | null
  ended_at: string | null
  error: Record<string, any> | null
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

export interface StatsOverview {
  total_traces: number
  total_spans: number
  error_rate: number
  avg_duration: number
  spans_by_type: Record<SpanType, number>
}

export interface SearchFilters {
  search?: string
  spanTypes?: SpanType[]
  hasErrors?: boolean
  dateRange?: [Date, Date] | null
  project?: string
}

export interface Conversation {
  group_id: string
  trace_count: number
  has_errors: boolean
  created_at: string
  workflow_name: string | null
  metadata: Record<string, any>
  first_input: string | null
}

export interface FailureCategory {
  id: number
  name: string
  description?: string | null
  created_at?: string
  usageCount?: number
}

export interface AnalysisData {
  markedForAnalysis: boolean
  judgment: 'acceptable' | 'unacceptable' | null
  notes: string
  analyzedAt?: string
  categories?: number[]  // Array of category IDs
}

export interface PlaygroundToolDefinition {
  name: string
  description: string
  schema?: Record<string, any> | null
  strict?: boolean | null
}

export type ModelSettingFieldType = 'number' | 'integer' | 'enum' | 'boolean'

export interface ModelSettingDefinition {
  key: string
  label: string
  type: ModelSettingFieldType
  min?: number
  max?: number
  step?: number
  options?: string[]
}

export interface PlaygroundGuardrailDefinition {
  name: string
  label: string
}

export interface PlaygroundGuardrailMetadata {
  input: PlaygroundGuardrailDefinition[]
  output: PlaygroundGuardrailDefinition[]
  default_input?: string[]
  default_output?: string[]
}

export interface PlaygroundStructuredOutputOption {
  value: string
  label: string
}

export interface PlaygroundStructuredOutputMetadata {
  default_type: string
  default_label: string
  default_strict: boolean
  default_schema?: Record<string, any> | null
  options: PlaygroundStructuredOutputOption[]
}

export interface PlaygroundSafeAgentOptions {
  skip_approvals?: boolean
  halt_on_rejection?: boolean
  safe_tool_names?: string[]
  safe_tool_patterns?: string[]
}

export interface PlaygroundSafeDefaults {
  use_safe_agent: boolean
  options?: PlaygroundSafeAgentOptions
}

export interface PlaygroundMetadata {
  available: boolean
  project_name?: string
  entry_agent?: string
  agent_key?: string
  agent_label?: string
  default_model?: string
  default_instructions?: string
  default_tools?: string[]
  tools?: PlaygroundToolDefinition[]
  requires_model_input?: boolean
  playground_ws_url?: string
  model_settings_schema?: ModelSettingDefinition[]
  default_model_settings?: Record<string, any>
  guardrails?: PlaygroundGuardrailMetadata
  safe_defaults?: PlaygroundSafeDefaults
  structured_output?: PlaygroundStructuredOutputMetadata
}

export interface PlaygroundSessionResponse {
  session_id: string
}

export interface AgentSummary {
  key: string
  name: string | null
  label: string
  description: string
  model: string
  is_entry: boolean
}

export interface EvaluationDimensionValue {
  name: string
  label?: string
  description?: string
  examples?: string[]
}

export interface EvaluationDimension {
  name: string
  label?: string
  description?: string
  values: EvaluationDimensionValue[]
}

export interface EvaluationDimensionsResponse {
  project?: string | null
  dimensions: EvaluationDimension[]
}

export interface StudioProjectMetadata {
  available: boolean
  project?: string | null
  manifest_path?: string | null
}

export interface EvaluationScenarioEntry {
  id?: string | null
  name: string
  prompt: string
  dimensions?: Record<string, any> | null
  metadata?: Record<string, any> | null
}

export interface EvaluationScenarioSet {
  kind: 'golden' | 'generated' | 'annotated'
  label: string
  path: string
  scenario_count: number
  updated_at?: string | null
  metadata?: Record<string, any> | null
  scenarios: EvaluationScenarioEntry[]
}

export interface EvaluationScenariosResponse {
  project?: string | null
  golden: EvaluationScenarioSet[]
  generated: EvaluationScenarioSet[]
  annotated: EvaluationScenarioSet[]
}

export interface EvaluationScenarioPromotionPayload {
  source_kind: 'generated' | 'annotated'
  set_path: string
  scenario_id?: string
  scenario_name?: string
  index?: number
}

export interface EvaluationScenarioPromotionResponse {
  project?: string | null
  promoted: EvaluationScenarioEntry
  golden: EvaluationScenarioSet[]
}

export interface EvaluationScenarioInput {
  id?: string | null
  name: string
  prompt: string
  dimensions?: Record<string, any> | null
}

export interface EvaluationScenarioCreatePayload {
  kind: 'golden' | 'generated' | 'annotated'
  set_path: string
  scenario: EvaluationScenarioInput
}

export interface EvaluationScenarioUpdatePayload extends EvaluationScenarioCreatePayload {
  scenario_id?: string | null
  scenario_name?: string
  index?: number
}

export interface EvaluationScenarioDeletePayload {
  kind: 'golden' | 'generated' | 'annotated'
  set_path: string
  scenario_id?: string | null
  scenario_name?: string
  index?: number
}

export interface EvaluationScenarioMutationResponse {
  project?: string | null
  set: EvaluationScenarioSet
}
