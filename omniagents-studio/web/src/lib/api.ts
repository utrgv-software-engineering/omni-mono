import type {
  AgentDescription,
  AgentSummary,
  AnalysisData,
  FailureCategory,
  StudioProjectMetadata,
  Trace,
  TraceWithSpans,
} from '../types'
import type {
  AgentOptions,
  AgentSource,
  Dimension,
  Holdout,
  Job,
  JobRequest,
  Judge,
  JudgeDataset,
  JudgeDatasetSplitDetail,
  Measure,
  RunComparison,
  SavedRun,
  SavedRunEntry,
  Scenario,
  ScenarioMatch,
  ScenarioSet,
} from '../types/evaluation'

/** A refused request: the server's reason, and an agent edit's problems. */
export class ApiError extends Error {
  readonly status: number
  readonly problems: string[]

  constructor(status: number, message: string, problems: string[] = []) {
    super(message)
    this.status = status
    this.problems = problems
  }
}

async function errorFrom(response: Response): Promise<ApiError> {
  let detail: unknown = null
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail
  } catch {
    detail = null
  }
  if (detail && typeof detail === 'object' && Array.isArray((detail as { problems?: unknown }).problems)) {
    const problems = (detail as { problems: string[] }).problems
    return new ApiError(response.status, problems.join('\n'), problems)
  }
  return new ApiError(response.status, typeof detail === 'string' && detail ? detail : response.statusText)
}

const API_BASE = import.meta.env.VITE_API_URL || `${window.location.origin}/api`

const ABSOLUTE_URL_RE = /^(https?|wss?):/i

/**
 * Split a configured API base into a credential-free base URL and the
 * bearer token it may carry (`VITE_API_URL=...?token=<secret>` is the
 * documented way to hand the web client a token). The token never
 * travels in a URL again after this point: HTTP requests attach it as an
 * `Authorization` header and WebSocket dials exchange it for a one-time
 * connect ticket via `POST /api/auth/ws-ticket`.
 */
export function splitBaseAndToken(rawBase: string): { base: string; token: string | null } {
  try {
    const url = ABSOLUTE_URL_RE.test(rawBase)
      ? new URL(rawBase)
      : new URL(rawBase, window.location.origin)
    const token = url.searchParams.get('token')
    if (!token) {
      return { base: rawBase, token: null }
    }
    url.searchParams.delete('token')
    if (ABSOLUTE_URL_RE.test(rawBase)) {
      return { base: url.toString().replace(/\?$/, ''), token }
    }
    return { base: `${url.pathname}${url.search}`, token }
  } catch {
    return { base: rawBase, token: null }
  }
}

export class ApiClient {
  private baseUrl: string
  private authToken: string | null

  constructor(baseUrl: string = API_BASE) {
    const { base, token } = splitBaseAndToken(baseUrl)
    this.baseUrl = base
    this.authToken = token
  }

  private authHeaders(): Record<string, string> {
    return this.authToken ? { Authorization: `Bearer ${this.authToken}` } : {}
  }

  private async fetch<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...this.authHeaders(),
        ...options?.headers,
      },
    })

    if (!response.ok) {
      throw await errorFrom(response)
    }

    return response.json()
  }

  /**
   * Resolve the configured base into an absolute URL. Relative bases
   * (the production default `/api`) resolve against the current origin.
   */
  private resolveApiBase(): URL {
    const base = this.baseUrl || '/api'
    return ABSOLUTE_URL_RE.test(base) ? new URL(base) : new URL(base, window.location.origin)
  }

  /**
   * Exchange the bearer token for a one-time WebSocket connect ticket.
   * Browsers cannot set WebSocket headers, so the ticket (single-use,
   * short-lived, bound to the minting identity) is the only credential
   * that ever appears in a WebSocket URL. Returns null when no token is
   * configured — unauthenticated deployments dial without credentials.
   */
  private async mintWsTicket(httpApiBase: string): Promise<string | null> {
    if (!this.authToken) {
      return null
    }
    const response = await fetch(`${httpApiBase}/auth/ws-ticket`, {
      method: 'POST',
      headers: this.authHeaders(),
    })
    if (!response.ok) {
      throw new Error(`Failed to obtain WebSocket connect ticket: ${response.statusText}`)
    }
    const body = await response.json()
    if (!body || typeof body.ticket !== 'string' || !body.ticket) {
      throw new Error('WebSocket connect ticket response was malformed')
    }
    return body.ticket
  }

  /**
   * Open a WebSocket to `<api base><path>`, authenticating with a
   * one-time connect ticket when a token is configured. Never puts the
   * bearer token itself in the URL.
   */
  private async openWebSocket(path: string, params?: Record<string, string>): Promise<WebSocket> {
    const resolved = this.resolveApiBase()
    const basePath = resolved.pathname.replace(/\/$/, '') || '/api'

    const httpProtocol =
      resolved.protocol === 'wss:' ? 'https:' : resolved.protocol === 'ws:' ? 'http:' : resolved.protocol
    const ticket = await this.mintWsTicket(`${httpProtocol}//${resolved.host}${basePath}`)

    const wsUrl = new URL(resolved.toString())
    wsUrl.protocol =
      resolved.protocol === 'https:' ? 'wss:' : resolved.protocol === 'http:' ? 'ws:' : resolved.protocol
    wsUrl.pathname = `${basePath}${path}`
    wsUrl.search = ''
    if (ticket) {
      wsUrl.searchParams.set('ticket', ticket)
    }
    Object.entries(params ?? {}).forEach(([key, value]) => {
      wsUrl.searchParams.set(key, value)
    })
    return new WebSocket(wsUrl.toString())
  }

  async getProjects(): Promise<{ projects: string[] }> {
    return this.fetch('/projects')
  }

  async getConversationTraces(
    project: string,
    groupId: string,
    options?: { includePreview?: boolean },
  ): Promise<{
    traces: Trace[]
    group_id: string
    total: number
  }> {
    const qs = options?.includePreview ? '?include_preview=true' : ''
    return this.fetch(`/conversations/${project}/${groupId}/traces${qs}`)
  }

  async getTrace(project: string, traceId: string): Promise<TraceWithSpans> {
    return this.fetch(`/traces/${project}/${traceId}`)
  }

  async getAllAnalysis(project: string, workflow_name?: string): Promise<Record<string, AnalysisData>> {
    const searchParams = new URLSearchParams()
    if (workflow_name) searchParams.append('workflow_name', workflow_name)
    const qs = searchParams.toString()
    return this.fetch(`/analysis/${project}${qs ? `?${qs}` : ''}`)
  }

  async updateAnalysis(
    project: string,
    groupId: string,
    analysis: AnalysisData
  ): Promise<AnalysisData> {
    return this.fetch(`/analysis/${project}/${groupId}`, {
      method: 'PUT',
      body: JSON.stringify(analysis),
    })
  }

  async getAnalysisCategories(project: string, groupId: string): Promise<{ categoryIds: string[] }> {
    return this.fetch(`/analysis/${project}/${groupId}/categories`)
  }

  async updateAnalysisCategories(
    project: string,
    groupId: string,
    categoryIds: string[]
  ): Promise<{ categoryIds: string[] }> {
    return this.fetch(`/analysis/${project}/${groupId}/categories`, {
      method: 'PUT',
      body: JSON.stringify({ categoryIds }),
    })
  }

  private send<T>(path: string, method: string, body?: unknown): Promise<T> {
    return this.fetch(path, { method, body: body === undefined ? undefined : JSON.stringify(body) })
  }

  async getStudioProject(): Promise<StudioProjectMetadata> {
    return this.fetch('/studio/project')
  }

  // -- agents ------------------------------------------------------------

  async getAgents(): Promise<{ agents: AgentSummary[] }> {
    return this.fetch('/agents')
  }

  /** What a run of the agent is given, from its runtime. */
  async describeAgent(agentKey: string): Promise<AgentDescription> {
    return this.fetch(`/agents/${encodeURIComponent(agentKey)}`)
  }

  /** What an agent can name: the project's capabilities, tools, guardrails, agents. */
  async agentOptions(): Promise<AgentOptions> {
    return this.fetch('/agents/options')
  }

  async agentSource(agentKey: string): Promise<AgentSource> {
    return this.fetch(`/agents/${encodeURIComponent(agentKey)}/source`)
  }

  /** Change settings (`null` removes one) and/or the instructions. */
  async editAgent(
    agentKey: string,
    edit: { changes?: Record<string, unknown>; instructions?: string | null },
  ): Promise<AgentSource> {
    return this.send(`/agents/${encodeURIComponent(agentKey)}`, 'PATCH', edit)
  }

  async replaceAgentYaml(agentKey: string, yaml: string): Promise<AgentSource> {
    return this.send(`/agents/${encodeURIComponent(agentKey)}/source`, 'PUT', { yaml })
  }

  async createAgent(agent: { key: string; model: string; label?: string; description?: string }): Promise<AgentSource> {
    return this.send('/agents', 'POST', agent)
  }

  async deleteAgent(agentKey: string): Promise<{ removed: string; entrypoint: string }> {
    return this.send(`/agents/${encodeURIComponent(agentKey)}`, 'DELETE')
  }

  /** Where the agent's real runtime is mounted, and the credential that
   * reaches it: the shared chat UI dials its `ws` there. */
  agentRuntime(agentKey: string): { baseUrl: string; authToken?: string } {
    const base = this.resolveApiBase()
    base.pathname = `${base.pathname.replace(/\/$/, '')}/agents/${encodeURIComponent(agentKey)}/runtime/`
    return { baseUrl: base.toString(), authToken: this.authToken ?? undefined }
  }

  // -- evaluation: what a scenario probes and how it is scored -------------

  async getDimensions(): Promise<{ dimensions: Dimension[] }> {
    return this.fetch('/evaluation/dimensions')
  }

  async setDimensions(dimensions: Dimension[]): Promise<{ dimensions: Dimension[] }> {
    return this.send('/evaluation/dimensions', 'PUT', { dimensions })
  }

  async getCategories(): Promise<{ categories: FailureCategory[] }> {
    return this.fetch('/evaluation/categories')
  }

  async addCategory(category: { name: string; description?: string | null; measures?: string[] }): Promise<FailureCategory> {
    return this.send('/evaluation/categories', 'POST', category)
  }

  async updateCategory(id: string, change: { description?: string | null; measures?: string[] | null }): Promise<FailureCategory> {
    return this.send(`/evaluation/categories/${encodeURIComponent(id)}`, 'PATCH', change)
  }

  async removeCategory(id: string): Promise<{ removed_from_scenarios: number }> {
    return this.send(`/evaluation/categories/${encodeURIComponent(id)}`, 'DELETE')
  }

  async getMeasures(): Promise<{ measures: Measure[] }> {
    return this.fetch('/evaluation/measures')
  }

  async addMeasure(measure: { name: string; label?: string | null; description?: string | null; stub?: boolean }): Promise<Measure> {
    return this.send('/evaluation/measures', 'POST', measure)
  }

  async updateMeasure(name: string, change: { label?: string | null; description?: string | null }): Promise<Measure> {
    return this.send(`/evaluation/measures/${encodeURIComponent(name)}`, 'PATCH', change)
  }

  async removeMeasure(name: string): Promise<{ removed: string }> {
    return this.send(`/evaluation/measures/${encodeURIComponent(name)}`, 'DELETE')
  }

  // -- evaluation: scenarios -----------------------------------------------

  async getScenarioSets(): Promise<{ sets: ScenarioSet[]; evaluations_dir: string }> {
    return this.fetch('/evaluation/scenario-sets')
  }

  async addScenario(setPath: string, scenario: Scenario): Promise<ScenarioSet> {
    return this.send('/evaluation/scenario-sets/scenarios', 'POST', { set_path: setPath, scenario })
  }

  async updateScenario(setPath: string, match: ScenarioMatch, scenario: Scenario): Promise<ScenarioSet> {
    return this.send('/evaluation/scenario-sets/scenarios', 'PUT', { set_path: setPath, match, scenario })
  }

  async removeScenario(setPath: string, match: ScenarioMatch): Promise<ScenarioSet> {
    return this.send('/evaluation/scenario-sets/scenarios/remove', 'POST', { set_path: setPath, match })
  }

  async promoteScenario(setPath: string, match: ScenarioMatch): Promise<ScenarioSet> {
    return this.send('/evaluation/scenario-sets/scenarios/promote', 'POST', { set_path: setPath, match })
  }

  async removeScenarioSet(path: string): Promise<{ removed: string }> {
    return this.send(`/evaluation/scenario-sets?${new URLSearchParams({ path })}`, 'DELETE')
  }

  // -- evaluation: judges ----------------------------------------------------

  async getJudges(): Promise<{ judges: Judge[] }> {
    return this.fetch('/evaluation/judges')
  }

  async addJudge(judge: {
    key: string
    model: string
    source?: string
    failure_category?: string | null
    label?: string | null
    instructions?: string | null
    template?: string | null
  }): Promise<Judge> {
    return this.send('/evaluation/judges', 'POST', judge)
  }

  async updateJudge(
    key: string,
    change: Partial<Pick<Judge, 'model' | 'source' | 'failure_category' | 'label' | 'instructions' | 'template'>>,
  ): Promise<Judge> {
    return this.send(`/evaluation/judges/${encodeURIComponent(key)}`, 'PATCH', change)
  }

  async removeJudge(key: string): Promise<{ removed: string }> {
    return this.send(`/evaluation/judges/${encodeURIComponent(key)}`, 'DELETE')
  }

  async getJudgeDatasets(): Promise<{ datasets: JudgeDataset[] }> {
    return this.fetch('/evaluation/judge-datasets')
  }

  async createJudgeDataset(dataset: {
    category: string
    fractions?: [number, number, number]
    seed?: number
  }): Promise<JudgeDataset> {
    return this.send('/evaluation/judge-datasets', 'POST', dataset)
  }

  async getJudgeDatasetSplit(path: string): Promise<JudgeDatasetSplitDetail> {
    return this.fetch(`/evaluation/judge-datasets/split?${new URLSearchParams({ path })}`)
  }

  async removeJudgeDataset(category: string): Promise<{ removed: string }> {
    return this.send(`/evaluation/judge-datasets?${new URLSearchParams({ category })}`, 'DELETE')
  }

  // -- evaluation: holdout and saved runs ------------------------------------

  async getHoldout(): Promise<{ holdout: Holdout | null }> {
    return this.fetch('/evaluation/holdout')
  }

  async createHoldout(holdout: {
    strata: string[]
    fractions: [number, number, number]
    seed: number
    select?: string | null
    exclude?: string | null
  }): Promise<{ holdout: Holdout }> {
    return this.send('/evaluation/holdout', 'POST', holdout)
  }

  async getRuns(): Promise<{ runs: SavedRunEntry[] }> {
    return this.fetch('/evaluation/runs')
  }

  async getRun(path: string): Promise<SavedRun> {
    return this.fetch(`/evaluation/runs/detail?${new URLSearchParams({ path })}`)
  }

  async compareRuns(a: string, b: string): Promise<RunComparison> {
    return this.fetch(`/evaluation/runs/compare?${new URLSearchParams({ a, b })}`)
  }

  async keepRun(path: string, name: string): Promise<SavedRunEntry> {
    return this.send('/evaluation/baselines', 'POST', { path, name })
  }

  async removeBaseline(name: string): Promise<{ removed: string }> {
    return this.send(`/evaluation/baselines/${encodeURIComponent(name)}`, 'DELETE')
  }

  // -- evaluation: jobs --------------------------------------------------------

  async startJob(request: JobRequest): Promise<Job> {
    return this.send('/evaluation/jobs', 'POST', request)
  }

  async getJob(jobId: string): Promise<Job> {
    return this.fetch(`/evaluation/jobs/${encodeURIComponent(jobId)}`)
  }

  async getJobs(): Promise<{ jobs: Job[] }> {
    return this.fetch('/evaluation/jobs')
  }

  /** A socket sending the job each time it changes, until it finishes. */
  followJob(jobId: string): Promise<WebSocket> {
    return this.openWebSocket(`/evaluation/jobs/${encodeURIComponent(jobId)}/ws`)
  }
}

export const api = new ApiClient()
export default api
