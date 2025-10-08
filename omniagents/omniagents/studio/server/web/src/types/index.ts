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

export interface AgentUpdatePayload {
  model: string
  instructions?: string
  tools: string[]
  model_settings?: Record<string, number | string | boolean>
  use_safe_agent: boolean
  safe_agent_options?: PlaygroundSafeAgentOptions
  guardrails?: {
    input?: string[]
    output?: string[]
  }
  output_type?: string | null
  output_type_strict?: boolean
  output_schema?: Record<string, any> | null
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

export interface EvaluationScenarioAnnotatePayload {
  kind: 'generated' | 'annotated'
  set_path: string
  agent?: string | null
}

export interface EvaluationScenarioAnnotateResponse {
  project?: string | null
  annotated_path: string
  set: EvaluationScenarioSet
}

export interface EvaluationScenarioSetDeletePayload {
  kind: 'generated' | 'annotated'
  set_path: string
}

export interface EvaluationScenarioSetDeleteResponse {
  project?: string | null
  deleted_path: string
  generated: EvaluationScenarioSet[]
  annotated: EvaluationScenarioSet[]
}

export type EvaluationScenarioRunState = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface EvaluationScenarioRunRequest {
  kind: 'golden' | 'generated' | 'annotated'
  set_path: string
  agent?: string | null
  select?: string | null
  exclude?: string | null
  max_turns?: number | null
  include_history?: boolean
  output_path?: string | null
}

export interface EvaluationScenarioRunStatus {
  job_id: string
  project?: string | null
  kind: 'golden' | 'generated' | 'annotated'
  set_path: string
  include_history: boolean
  max_turns?: number | null
  output_path?: string | null
  select?: string | null
  exclude?: string | null
  state: EvaluationScenarioRunState
  total: number
  completed: number
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
  runs_path?: string | null
  workflow_name?: string | null
  error?: string | null
}

export interface EvaluationScenarioRunResponse {
  job_id: string
  status?: EvaluationScenarioRunStatus | null
}

export type EvaluationScenarioGenerationState = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface EvaluationScenarioGenerationRequest {
  name?: string | null
  output_path?: string | null
  select?: string | null
  exclude?: string | null
  count?: number | null
  seed?: number | null
  agent?: string | null
  dimensions?: Record<string, string[]> | null
}

export interface EvaluationScenarioGenerationStatus {
  job_id: string
  project?: string | null
  name?: string | null
  output_path?: string | null
  select?: string | null
  exclude?: string | null
  count?: number | null
  seed?: number | null
  dimensions?: Record<string, string[]> | null
  state: EvaluationScenarioGenerationState
  total: number
  completed: number
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
  scenarios_path?: string | null
  workflow_name?: string | null
  error?: string | null
}

export interface EvaluationScenarioGenerationResponse {
  job_id: string
  status?: EvaluationScenarioGenerationStatus | null
}

export type EvaluationDatasetSourceKind = 'golden' | 'generated' | 'annotated' | 'unknown'

export interface EvaluationDataset {
  label: string
  path: string
  source_kind: EvaluationDatasetSourceKind
  source_label?: string | null
  source_path?: string | null
  run_count: number
  scenario_count?: number | null
  expected_count?: number | null
  includes_history: boolean
  updated_at?: string | null
  created_at?: string | null
  workflow_generate?: string | null
  workflow_run?: string | null
  workflow_run_at?: string | null
  manifest_path?: string | null
  size_bytes?: number | null
}

export interface EvaluationDatasetsResponse {
  project?: string | null
  datasets: EvaluationDataset[]
}
