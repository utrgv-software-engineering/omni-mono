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