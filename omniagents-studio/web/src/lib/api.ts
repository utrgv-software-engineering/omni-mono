import type {
  Trace,
  TraceWithSpans,
  FailureCategory,
  AnalysisData,
  AgentDescription,
  AgentSummary,
  EvaluationDimension,
  EvaluationDimensionsResponse,
  StudioProjectMetadata,
  EvaluationScenariosResponse,
  EvaluationScenarioPromotionPayload,
  EvaluationScenarioPromotionResponse,
  EvaluationScenarioCreatePayload,
  EvaluationScenarioUpdatePayload,
  EvaluationScenarioDeletePayload,
  EvaluationScenarioMutationResponse,
  EvaluationScenarioRunRequest,
  EvaluationScenarioRunResponse,
  EvaluationScenarioGenerationRequest,
  EvaluationScenarioGenerationResponse,
  EvaluationDatasetsResponse,
  EvaluationDatasetSplitDetails,
  EvaluationScenarioSplitPayload,
  EvaluationScenarioSplitResponse,
  EvaluationDatasetDeletePayload,
  EvaluationDatasetDeleteResponse,
  EvaluationScenarioSetDeletePayload,
  EvaluationScenarioSetDeleteResponse,
  EvaluationJudgeCreatePayload,
  EvaluationJudgeUpdatePayload,
  EvaluationJudgesResponse,
  EvaluationJudgeDeleteResponse,
  EvaluationJudgeOptimizationRequest,
  EvaluationJudgeOptimizationResponse,
  EvaluationAgentOptimizationRequest,
  EvaluationAgentOptimizationResponse,
  EvaluationSuiteRunRequest,
  EvaluationSuiteRunResponse,
  EvaluationMeasuresResponse,
  EvaluationMeasureCreatePayload,
  EvaluationMeasureUpdatePayload,
  EvaluationMeasureDeleteResponse,
  EvaluationResultsListResponse,
  EvaluationResultsDetailResponse,
} from '../types'

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
      throw new Error(`API Error: ${response.statusText}`)
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

  async getFailureCategories(project: string): Promise<FailureCategory[]> {
    return this.fetch(`/categories/${project}`)
  }

  async createFailureCategory(
    project: string,
    category: { name: string; description?: string | null; measures?: string[] | null }
  ): Promise<FailureCategory> {
    return this.fetch(`/categories/${project}`, {
      method: 'POST',
      body: JSON.stringify(category),
    })
  }

  async updateFailureCategory(
    project: string,
    categorySlug: string,
    payload: { description?: string | null; measures?: string[] | null }
  ): Promise<FailureCategory> {
    const encoded = encodeURIComponent(categorySlug)
    return this.fetch(`/categories/${project}/${encoded}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  }

  async deleteFailureCategory(
    project: string,
    categorySlug: string
  ): Promise<{ message: string; previousUsageCount: number }> {
    const encoded = encodeURIComponent(categorySlug)
    return this.fetch(`/categories/${project}/${encoded}`, {
      method: 'DELETE',
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

  async getAgents(): Promise<{ agents: AgentSummary[] }> {
    return this.fetch('/agents')
  }

  /** What a run of the agent is given, from its runtime. */
  async describeAgent(agentKey: string): Promise<AgentDescription> {
    return this.fetch(`/agents/${encodeURIComponent(agentKey)}`)
  }

  /** Where the agent's real runtime is mounted, and the credential that
   * reaches it: the shared chat UI dials its `ws` there. */
  agentRuntime(agentKey: string): { baseUrl: string; authToken?: string } {
    const base = this.resolveApiBase()
    base.pathname = `${base.pathname.replace(/\/$/, '')}/agents/${encodeURIComponent(agentKey)}/runtime/`
    return { baseUrl: base.toString(), authToken: this.authToken ?? undefined }
  }

  async getEvaluationDimensions(): Promise<EvaluationDimensionsResponse> {
    return this.fetch('/evaluation/dimensions')
  }

  async updateEvaluationDimensions(dimensions: EvaluationDimension[]): Promise<EvaluationDimensionsResponse> {
    return this.fetch('/evaluation/dimensions', {
      method: 'PUT',
      body: JSON.stringify({ dimensions }),
    })
  }

  async getStudioProject(): Promise<StudioProjectMetadata> {
    return this.fetch('/studio/project')
  }

  async getEvaluationScenarios(): Promise<EvaluationScenariosResponse> {
    return this.fetch('/evaluation/scenarios')
  }

  async getEvaluationDatasets(): Promise<EvaluationDatasetsResponse> {
    return this.fetch('/evaluation/datasets')
  }

  async getEvaluationDatasetSplit(path: string): Promise<EvaluationDatasetSplitDetails> {
    const searchParams = new URLSearchParams({ path })
    return this.fetch(`/evaluation/datasets/split?${searchParams.toString()}`)
  }

  async getEvaluationMeasures(): Promise<EvaluationMeasuresResponse> {
    return this.fetch('/evaluation/measures')
  }

  async createEvaluationMeasure(payload: EvaluationMeasureCreatePayload): Promise<EvaluationMeasuresResponse> {
    return this.fetch('/evaluation/measures', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async updateEvaluationMeasure(payload: EvaluationMeasureUpdatePayload): Promise<EvaluationMeasuresResponse> {
    return this.fetch('/evaluation/measures', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  }

  async deleteEvaluationMeasure(name: string): Promise<EvaluationMeasureDeleteResponse> {
    const encoded = encodeURIComponent(name)
    return this.fetch(`/evaluation/measures/${encoded}`, {
      method: 'DELETE',
    })
  }

  async getEvaluationJudges(): Promise<EvaluationJudgesResponse> {
    return this.fetch('/evaluation/judges')
  }

  async createEvaluationJudge(payload: EvaluationJudgeCreatePayload): Promise<EvaluationJudgesResponse> {
    return this.fetch('/evaluation/judges', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async updateEvaluationJudge(payload: EvaluationJudgeUpdatePayload): Promise<EvaluationJudgesResponse> {
    return this.fetch('/evaluation/judges', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  }

  async deleteEvaluationJudge(key: string): Promise<EvaluationJudgeDeleteResponse> {
    const encoded = encodeURIComponent(key)
    return this.fetch(`/evaluation/judges/${encoded}`, {
      method: 'DELETE',
    })
  }

  async startJudgeOptimization(payload: EvaluationJudgeOptimizationRequest): Promise<EvaluationJudgeOptimizationResponse> {
    return this.fetch('/evaluation/judges/optimize', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async getJudgeOptimizationStatus(jobId: string): Promise<EvaluationJudgeOptimizationResponse> {
    return this.fetch(`/evaluation/judges/optimize/${jobId}`)
  }

  async startAgentOptimization(payload: EvaluationAgentOptimizationRequest): Promise<EvaluationAgentOptimizationResponse> {
    return this.fetch('/evaluation/agents/optimize', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async getAgentOptimizationStatus(jobId: string): Promise<EvaluationAgentOptimizationResponse> {
    return this.fetch(`/evaluation/agents/optimize/${jobId}`)
  }

  async splitEvaluationScenario(payload: EvaluationScenarioSplitPayload): Promise<EvaluationScenarioSplitResponse> {
    return this.fetch('/evaluation/scenarios/split', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async deleteEvaluationDataset(payload: EvaluationDatasetDeletePayload): Promise<EvaluationDatasetDeleteResponse> {
    return this.fetch('/evaluation/datasets', {
      method: 'DELETE',
      body: JSON.stringify(payload),
    })
  }

  async promoteEvaluationScenario(payload: EvaluationScenarioPromotionPayload): Promise<EvaluationScenarioPromotionResponse> {
    return this.fetch('/evaluation/scenarios/promote', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async deleteEvaluationScenarioSet(payload: EvaluationScenarioSetDeletePayload): Promise<EvaluationScenarioSetDeleteResponse> {
    return this.fetch('/evaluation/scenarios/delete', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async createEvaluationScenario(payload: EvaluationScenarioCreatePayload): Promise<EvaluationScenarioMutationResponse> {
    return this.fetch('/evaluation/scenarios/item', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async updateEvaluationScenario(payload: EvaluationScenarioUpdatePayload): Promise<EvaluationScenarioMutationResponse> {
    return this.fetch('/evaluation/scenarios/item', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  }

  async deleteEvaluationScenario(payload: EvaluationScenarioDeletePayload): Promise<EvaluationScenarioMutationResponse> {
    return this.fetch('/evaluation/scenarios/item', {
      method: 'DELETE',
      body: JSON.stringify(payload),
    })
  }

  async startEvaluationScenarioRun(payload: EvaluationScenarioRunRequest): Promise<EvaluationScenarioRunResponse> {
    return this.fetch('/evaluation/scenarios/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async getEvaluationScenarioRunStatus(jobId: string): Promise<EvaluationScenarioRunResponse> {
    return this.fetch(`/evaluation/scenarios/run/${jobId}`)
  }

  async startEvaluationScenarioGeneration(payload: EvaluationScenarioGenerationRequest): Promise<EvaluationScenarioGenerationResponse> {
    return this.fetch('/evaluation/scenarios/generate', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async getEvaluationScenarioGenerationStatus(jobId: string): Promise<EvaluationScenarioGenerationResponse> {
    return this.fetch(`/evaluation/scenarios/generate/${jobId}`)
  }

  async startEvaluationSuiteRun(payload: EvaluationSuiteRunRequest): Promise<EvaluationSuiteRunResponse> {
    return this.fetch('/evaluation/suite/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async getEvaluationSuiteRunStatus(jobId: string): Promise<EvaluationSuiteRunResponse> {
    return this.fetch(`/evaluation/suite/run/${jobId}`)
  }

  async getEvaluationResults(): Promise<EvaluationResultsListResponse> {
    return this.fetch('/evaluation/results')
  }

  async getEvaluationResultsDetail(path: string): Promise<EvaluationResultsDetailResponse> {
    const searchParams = new URLSearchParams({ path })
    return this.fetch(`/evaluation/results/detail?${searchParams.toString()}`)
  }

  createScenarioRunWebSocket(jobId: string): Promise<WebSocket> {
    return this.openWebSocket(`/evaluation/scenarios/run/${jobId}/ws`)
  }

  createSuiteRunWebSocket(jobId: string): Promise<WebSocket> {
    return this.openWebSocket(`/evaluation/suite/run/${jobId}/ws`)
  }

  createScenarioGenerationWebSocket(jobId: string): Promise<WebSocket> {
    return this.openWebSocket(`/evaluation/scenarios/generate/${jobId}/ws`)
  }

  createJudgeOptimizationWebSocket(jobId: string): Promise<WebSocket> {
    return this.openWebSocket(`/evaluation/judges/optimize/${jobId}/ws`)
  }

  createAgentOptimizationWebSocket(jobId: string): Promise<WebSocket> {
    return this.openWebSocket(`/evaluation/agents/optimize/${jobId}/ws`)
  }

}

export const api = new ApiClient()
export default api
