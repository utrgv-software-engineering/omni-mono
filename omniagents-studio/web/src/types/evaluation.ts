import type { JsonValue } from './index'

/** One dimension synthetic scenarios are drawn from. */
export interface Dimension {
  name: string
  label: string | null
  description: string | null
  values: Array<{ name: string; label: string | null; description: string | null; examples: string[] }>
}

export interface Measure {
  name: string
  label: string
  description: string | null
  builtin: boolean
  /** Whether a function exists for it (builtin or in measures.py). */
  implemented: boolean
  source_path: string | null
  usage_count: number
}

/** A scenario as written in its file; fields beyond these are kept as is. */
export interface Scenario {
  id?: string
  name: string
  prompt?: string
  prompts?: string[]
  goal?: string
  tags?: string[]
  measures?: Array<string | Record<string, JsonValue>>
  failure_categories?: string[]
  dimensions?: Record<string, string>
  expect?: Record<string, JsonValue>
  label?: boolean
  [field: string]: JsonValue | undefined
}

export interface ScenarioSet {
  /** `project`: a file in scenarios/, which the suite runs. `generated`: a set in generated/. */
  kind: 'project' | 'generated'
  path: string
  label: string
  scenarios: Scenario[]
  updated_at: string | null
  manifest: Record<string, JsonValue> | null
}

/** Which scenario of a set an edit means: by id, else name, else position. */
export interface ScenarioMatch {
  id?: string | null
  name?: string | null
  index?: number | null
}

export interface Judge {
  key: string
  label: string
  model: string | null
  /** What the judge reads: the final reply or the whole conversation. */
  source: string
  failure_category: string | null
  instructions: string
  template: string
  instructions_path: string | null
  template_path: string | null
  defined_in: string
  /** Few-shot examples: answer ids from its dataset's train split. */
  examples: string[]
  /** Whether its model is a dated snapshot rather than a movable alias. */
  model_pinned: boolean
  /** Validated as it is now, edited since, or never validated. */
  status: 'validated' | 'changed' | 'unvalidated'
  /** Its one measurement on the judge dataset's test split (Pass positive). */
  validation: JudgeValidation | null
}

export interface JudgeValidation {
  judge_version: string
  model: string | null
  test: { tp: number; fn: number; tn: number; fp: number; tpr: number | null; tnr: number | null }
  source: string
  validated_at: string
}

/** What one judge's verdicts in a run say. */
export interface JudgeReport {
  judge: string
  status: 'validated' | 'changed' | 'unvalidated'
  judged: number
  answered: number
  passed: number
  raw_pass_rate: number | null
  /** Corrected for the judge's TPR and TNR; null unless validated as it is now. */
  corrected_pass_rate: number | null
  corrected_interval: [number, number] | null
  tpr?: number | null
  tnr?: number | null
}

export interface JudgeDatasetSplit {
  split: 'train' | 'dev' | 'test'
  path: string
  scenario_count: number
  /** Answers reviewers saw the category in: the judge should say Fail. */
  fails: number
  passes: number
  updated_at: string | null
}

/** A failure category's judge dataset: every reviewed answer, labelled
 * positive when reviewers saw the category, split into train/dev/test. */
export interface JudgeDataset {
  category: string
  manifest: {
    seed?: number
    fractions?: number[]
    built_at?: string
    /** Too few answers of a label to trust TPR and TNR. */
    warnings?: string[]
    [field: string]: JsonValue | undefined
  }
  splits: JudgeDatasetSplit[]
}

export interface JudgeDatasetSplitDetail extends JudgeDataset {
  split: JudgeDatasetSplit
  /** Each reviewed answer: the scenario, its label and the reply as reviewed. */
  scenarios: Array<Scenario & { reply: string | null; scenario_id: string }>
}

export interface Holdout {
  seed: number
  strata: string[]
  fractions: number[]
  total: number
  counts: Record<'train' | 'dev' | 'test', number>
  counts_by_stratum: Record<string, Record<string, number>>
  updated_at: string | null
}

/** A rate with its 95% Wilson interval. */
export interface Rate {
  value: number | null
  runs: number
  passed_runs: number
  interval: [number, number] | null
}

/** What a run listing shows: counts and the pass rate with its interval. */
export interface RunOverview {
  total_scenarios: number
  passed: number
  failed: number
  answered: number
  non_responses: number
  repeats: number
  pass_rate: number | null
  interval: [number, number] | null
  per_measure: Record<string, { passed: number; failed: number; total: number }>
}

/** What two runs must share to be compared, and what they used. */
export interface RunManifest {
  agent?: string | null
  model?: string | null
  tools?: string[]
  capabilities?: string[]
  instructions_sha?: string | null
  agent_yml_sha?: string | null
  max_turns?: number | null
  omniagents_version?: string
  select?: string | null
  exclude?: string | null
  split?: string | null
  holdout?: Record<string, JsonValue> | null
  repeats?: number
  scenarios?: number
  created_at?: string
  [field: string]: JsonValue | undefined
}

export interface SavedRunEntry {
  id: string
  timestamp: string
  path: string
  source?: string
  /** The set file it ran, relative to the evaluations directory; null for the suite. */
  set: string | null
  label: string
  /** The name it is kept under in baselines/ (committed); null for a run. */
  baseline: string | null
  workflow_name?: string | null
  agent: string | null
  /** Older runs carry only the counts. */
  summary: Partial<RunOverview>
  manifest?: RunManifest | null
}

export interface MeasureResult {
  name: string
  passed: boolean | null
  reason?: string | null
  tier?: string
  [field: string]: JsonValue | undefined
}

export interface RunRecord {
  scenario: string
  scenario_config: Scenario
  trial?: number
  repeats?: number
  passed?: boolean
  answered?: boolean
  measures: MeasureResult[]
  final_assistant_text?: string | null
  session_id?: string | null
  trace_group_id?: string | null
  error?: { type: string; message: string }
  exchanges?: number
  duration_seconds?: number
  usage?: Record<string, number>
  history?: JsonValue[]
  [field: string]: unknown
}

export interface Metric {
  name: string
  label?: string
  type?: string
  value: number | string | null
  interval?: [number, number] | null
  runs?: number
  target?: { op: string; value: number } | null
  meets_target?: boolean
  slices?: Record<string, Record<string, Rate>>
  [field: string]: unknown
}

export interface SavedRun {
  path: string
  metadata: Record<string, JsonValue>
  runs: RunRecord[]
  overview: RunOverview
  summary?: Rate & { answered: number }
  stats?: Record<string, JsonValue>
  metrics?: Metric[]
  manifest?: RunManifest | null
  warnings?: string[]
  judges?: JudgeReport[]
}

export interface RunComparison {
  comparable: boolean
  reason: string | null
  /** Manifest fields that changed: [run a, run b]. */
  differences: Record<string, [JsonValue, JsonValue]>
  pass_rate: {
    a: number | null
    a_interval: [number, number] | null
    b: number | null
    b_interval: [number, number] | null
    change: number | null
  }
  /** Scenarios whose pass rate changed (per scenario, across trials). */
  scenarios: Array<{ scenario: string; name: string | null; a: number | null; b: number | null }>
}

export type JobKind = 'run' | 'generate' | 'optimize_judge' | 'optimize_agent' | 'propose_modes' | 'suggest_labels'

export interface Job {
  id: string
  kind: JobKind
  params: Record<string, JsonValue>
  state: 'queued' | 'running' | 'completed' | 'failed'
  created_at: string
  started_at: string | null
  finished_at: string | null
  completed: number
  total: number | null
  log: string[]
  result: Record<string, JsonValue> | null
  error: string | null
}

export type JobRequest =
  | {
      kind: 'run'
      agent?: string | null
      scenario_set?: string | null
      select?: string | null
      exclude?: string | null
      split?: string | null
      include_unmeasured?: boolean | null
      max_turns?: number | null
      name?: string | null
    }
  | { kind: 'generate'; name: string; count?: number | null; seed?: number | null }
  | { kind: 'optimize_judge'; judge: string; category: string; max_metric_calls?: number; seed?: number }
  | { kind: 'propose_modes' }
  | { kind: 'suggest_labels'; category: string }
  | {
      kind: 'optimize_agent'
      agent: string
      category: string
      positives_only?: boolean
      max_metric_calls?: number
      seed?: number
    }

/** An agent's files, as `GET /api/agents/<key>/source` returns them. */
export interface AgentSource {
  key: string
  path: string
  yaml: string
  instructions: string
  /** null when the instructions are inline in agent.yml. */
  instructions_path: string | null
  /** Why agent.yml does not parse, when it does not. */
  error: string | null
  /** agent.yml's settings as plain data, null when it does not parse. */
  settings: Record<string, JsonValue> | null
}

export interface AgentOptions {
  capabilities: Array<{ name: string; description: string; tools: string[] }>
  tools: string[]
  guardrails: Record<'input' | 'output' | 'tool_input' | 'tool_output', string[]>
  agents: string[]
}

/** Unreviewed evaluation answers to read next, and why each was picked. */
export interface ReviewSample {
  drawn_at: string
  unreviewed: number
  items: Array<{ group_id: string; reason: string }>
}

/** Review notes grouped into a failure mode: an existing category, or a new one. */
export interface FailureModeProposal {
  id: string
  existing_category: string | null
  name: string
  description: string | null
  review_ids: string[]
  notes: string[]
}

/** An answer that may show a failure mode; a label only once accepted. */
export interface LabelSuggestion {
  id: string
  group_id: string
  category: string
  quote: string | null
  reason: string | null
  reviewed: boolean
  status: 'pending' | 'accepted' | 'dismissed'
}

export interface ReviewAssist {
  sample: ReviewSample | null
  proposals: FailureModeProposal[]
  suggestions: LabelSuggestion[]
}

/** A generated set drafted but not yet generated: the combinations of
 * dimension values a person reviews before any scenario is written. */
export interface Draft {
  name: string
  path: string
  combinations: Array<Record<string, string>>
  generated: boolean
}
