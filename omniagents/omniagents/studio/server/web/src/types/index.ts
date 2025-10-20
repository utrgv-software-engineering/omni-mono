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
  id: string
  name: string
  description?: string | null
  measures?: string[] | null
  usageCount?: number
}

export interface AnalysisData {
  markedForAnalysis: boolean
  judgment: 'acceptable' | 'unacceptable' | null
  notes: string
  analyzedAt?: string
  categories?: string[]
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

export interface AgentCreatePayload {
  name: string
  model?: string
}

export interface AgentCreateResponse {
  agent: AgentSummary
  metadata: PlaygroundMetadata
}

export interface AgentDeleteResponse {
  deleted: string
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
  measures?: any[] | null
  failure_categories?: string[] | null
  label?: boolean | null
  metadata?: Record<string, any> | null
  analysis?: EvaluationScenarioAnalysis | null
}

export interface EvaluationScenarioAnalysis {
  group_id?: string | null
  judgment?: 'acceptable' | 'unacceptable' | null
  notes?: string | null
  marked?: boolean | null
  analyzed_at?: string | null
  updated_at?: string | null
  workflow?: string | null
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

export type EvaluationSuiteRunState = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface EvaluationSuiteRunSummary {
  total_checks: number
  passed_checks: number
  failed_checks: number
  skipped_checks: number
  measures_total: number
  measures_failed: number
  measures_skipped: number
  metrics_total: number
  metrics_failed: number
  metrics_skipped: number
  scenarios_total: number
  scenarios_included: number
  scenarios_skipped: number
}

export interface EvaluationSuiteRunRequest {
  name?: string | null
  select?: string | null
  exclude?: string | null
  include_unmeasured?: boolean | null
  output_path?: string | null
  agent?: string | null
  reporter?: 'auto' | 'pretty' | 'tap' | null
  color?: 'auto' | 'always' | 'never' | null
}

export interface EvaluationSuiteRunStatus {
  job_id: string
  project?: string | null
  agent?: string | null
  name?: string | null
  select?: string | null
  exclude?: string | null
  include_unmeasured: boolean
  output_path?: string | null
  reporter?: string | null
  color?: string | null
  state: EvaluationSuiteRunState
  total: number
  completed: number
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
  result_path?: string | null
  report_path?: string | null
  workflow_name?: string | null
  summary?: EvaluationSuiteRunSummary | null
  error?: string | null
}

export interface EvaluationSuiteRunResponse {
  job_id: string
  status?: EvaluationSuiteRunStatus | null
}

export interface EvaluationDatasetSplit {
  split: 'train' | 'dev' | 'test'
  path: string
  scenario_count: number
  positives: number
  negatives: number
  updated_at?: string | null
}

export interface EvaluationDataset {
  set_name: string
  category: string
  artifact_dir: string
  scenario_set_path?: string | null
  manifest_path?: string | null
  splits: EvaluationDatasetSplit[]
}

export interface EvaluationDatasetsResponse {
  project?: string | null
  datasets: EvaluationDataset[]
}

export interface EvaluationDatasetSplitDetails {
  project?: string | null
  set_name: string
  category: string
  split: EvaluationDatasetSplit
  scenarios: EvaluationScenarioEntry[]
}

export interface EvaluationScenarioSplitPayload {
  set_path: string
  kind: 'golden' | 'generated' | 'annotated'
  category: string
  train?: number
  dev?: number
  test?: number
  seed?: number
  negatives_ratio?: number | null
  output_dir?: string | null
}

export interface EvaluationScenarioSplitResponse {
  project?: string | null
  dataset: EvaluationDataset
}

export interface EvaluationDatasetDeletePayload {
  set_name: string
  category: string
}

export interface EvaluationDatasetDeleteResponse {
  project?: string | null
  datasets: EvaluationDataset[]
}

export interface EvaluationMeasure {
  name: string
  label?: string | null
  description?: string | null
  module?: string | null
  sourcePath?: string | null
  docstring?: string | null
  hasFunction: boolean
  usageCount: number
}

export interface EvaluationMeasuresResponse {
  project?: string | null
  measures: EvaluationMeasure[]
}

export interface EvaluationMeasureCreatePayload {
  name: string
  label?: string | null
  description?: string | null
  createStub?: boolean
}

export interface EvaluationMeasureUpdatePayload {
  name: string
  label?: string | null
  description?: string | null
}

export interface EvaluationMeasureDeleteResponse {
  project?: string | null
  deleted: string
  measures: EvaluationMeasure[]
}

export interface EvaluationJudge {
  key: string
  agent_name?: string | null
  agent_label?: string | null
  model?: string | null
  source: string
  failure_category?: string | null
  instructions?: string | null
  template?: string | null
  instructions_file?: string | null
  template_file?: string | null
  instructions_path?: string | null
  template_path?: string | null
  judge_path: string
  updated_at?: string | null
}

export interface EvaluationJudgesResponse {
  project?: string | null
  judges: EvaluationJudge[]
}

export interface EvaluationJudgeCreatePayload {
  name?: string | null
  key?: string | null
  model?: string | null
  agent_label?: string | null
  agent_name?: string | null
  source?: string | null
  failure_category?: string | null
  instructions?: string | null
  template?: string | null
  add_measure?: boolean
}

export interface EvaluationJudgeUpdatePayload {
  key: string
  model?: string | null
  agent_label?: string | null
  agent_name?: string | null
  source?: string | null
  failure_category?: string | null
  instructions?: string | null
  template?: string | null
}

export interface EvaluationJudgeDeleteResponse {
  project?: string | null
  deleted: string
  judges: EvaluationJudge[]
}

export type EvaluationJudgeOptimizationState = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface EvaluationJudgeOptimizationDataset {
  set_name: string
  category: string
  artifact_dir?: string | null
  train_path: string
  dev_path: string
  test_path: string
  runs_path?: string | null
  source?: string | null
}

export interface EvaluationJudgeOptimizationRequest {
  judge_key: string
  dataset: EvaluationJudgeOptimizationDataset
  max_metric_calls?: number | null
  seed?: number | null
}

export interface EvaluationJudgeOptimizationStatus {
  job_id: string
  project?: string | null
  judge_key: string
  dataset: EvaluationJudgeOptimizationDataset
  max_metric_calls: number
  seed?: number | null
  state: EvaluationJudgeOptimizationState
  total: number
  completed: number
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
  run_dir?: string | null
  result_path?: string | null
  workflow_name?: string | null
  error?: string | null
  eta_seconds?: number | null
}

export interface EvaluationJudgeOptimizationResponse {
  job_id: string
  status?: EvaluationJudgeOptimizationStatus | null
}

export type EvaluationAgentOptimizationState = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface EvaluationAgentOptimizationDataset {
  set_name: string
  category: string
  artifact_dir?: string | null
  train_path: string
  dev_path: string
  test_path: string
}

export interface EvaluationAgentOptimizationRequest {
  agent_key: string
  dataset: EvaluationAgentOptimizationDataset
  positives_only?: boolean | null
  max_metric_calls?: number | null
  seed?: number | null
}

export interface EvaluationAgentOptimizationStatus {
  job_id: string
  project?: string | null
  agent_key: string
  dataset: EvaluationAgentOptimizationDataset
  positives_only: boolean
  max_metric_calls: number
  seed?: number | null
  state: EvaluationAgentOptimizationState
  total: number
  completed: number
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
  run_dir?: string | null
  result_path?: string | null
  workflow_name?: string | null
  error?: string | null
  eta_seconds?: number | null
}

export interface EvaluationAgentOptimizationResponse {
  job_id: string
  status?: EvaluationAgentOptimizationStatus | null
}
