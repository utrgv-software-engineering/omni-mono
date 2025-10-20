import type {
  Trace,
  Span,
  TraceWithSpans,
  StatsOverview,
  Conversation,
  FailureCategory,
  AnalysisData,
  PlaygroundMetadata,
  PlaygroundSessionResponse,
  PlaygroundSafeAgentOptions,
  AgentUpdatePayload,
  AgentSummary,
  AgentCreatePayload,
  AgentCreateResponse,
  AgentDeleteResponse,
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
} from '../types'

const API_BASE = import.meta.env.VITE_API_URL || `${window.location.origin}/api`

export interface PlaygroundSessionPayload {
  model: string
  instructions?: string
  tools?: string[]
  model_settings?: Record<string, number | string | boolean>
  use_safe_agent?: boolean
  safe_agent_options?: PlaygroundSafeAgentOptions
  guardrails?: {
    input?: string[]
    output?: string[]
  }
  output_type?: string
  output_type_strict?: boolean
  output_schema?: Record<string, any>
  session_id?: string
}

class ApiClient {
  private baseUrl: string

  constructor(baseUrl: string = API_BASE) {
    this.baseUrl = baseUrl
  }

  private async fetch<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    })

    if (!response.ok) {
      throw new Error(`API Error: ${response.statusText}`)
    }

    return response.json()
  }

  async getProjects(): Promise<{ projects: string[] }> {
    return this.fetch('/projects')
  }

  async getConversations(params: {
    project: string
    limit?: number
    offset?: number
    search?: string
    has_errors?: boolean
    workflow_name?: string
    dims?: string[]
  }): Promise<{ conversations: Conversation[]; total: number }> {
    const searchParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        if (key === 'dims' && Array.isArray(value)) {
          value.forEach(v => searchParams.append('dim', String(v)))
        } else {
          searchParams.append(key, String(value))
        }
      }
    })
    return this.fetch(`/conversations?${searchParams}`)
  }

  async getConversationTraces(project: string, groupId: string): Promise<{ 
    traces: Trace[]; 
    group_id: string; 
    total: number 
  }> {
    return this.fetch(`/conversations/${project}/${groupId}/traces`)
  }

  async getTraces(params: {
    project: string
    limit?: number
    offset?: number
    search?: string
    has_errors?: boolean
    span_type?: string
  }): Promise<{ traces: Trace[]; total: number }> {
    const searchParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        searchParams.append(key, String(value))
      }
    })
    return this.fetch(`/traces?${searchParams}`)
  }

  async getTrace(project: string, traceId: string): Promise<TraceWithSpans> {
    return this.fetch(`/traces/${project}/${traceId}`)
  }

  async search(project: string, query: string, type: 'traces' | 'spans' = 'traces'): Promise<{
    results: Array<Trace | Span>
    total: number
  }> {
    const searchParams = new URLSearchParams({ project, q: query, type })
    return this.fetch(`/search?${searchParams}`)
  }

  async getStats(project: string): Promise<StatsOverview> {
    const searchParams = new URLSearchParams({ project })
    return this.fetch(`/stats/overview?${searchParams}`)
  }

  async getWorkflows(project: string): Promise<{ workflows: string[] }> {
    const searchParams = new URLSearchParams({ project })
    return this.fetch(`/workflows?${searchParams}`)
  }

  async getSyntheticDimensions(project: string, workflow_name?: string): Promise<{ dimensions: { name: string; label: string; values: { name: string; label: string }[] }[] }> {
    const sp = new URLSearchParams({ project })
    if (workflow_name) sp.append('workflow_name', workflow_name)
    return this.fetch(`/synthetic_dimensions?${sp.toString()}`)
  }

  async exportTrace(project: string, traceId: string, format: 'json' | 'csv' = 'json'): Promise<Blob> {
    const response = await fetch(`${this.baseUrl}/export/${project}/${traceId}?format=${format}`)
    if (!response.ok) {
      throw new Error(`Export failed: ${response.statusText}`)
    }
    return response.blob()
  }

  async getAllAnalysis(project: string, workflow_name?: string): Promise<Record<string, AnalysisData>> {
    const searchParams = new URLSearchParams()
    if (workflow_name) searchParams.append('workflow_name', workflow_name)
    const qs = searchParams.toString()
    return this.fetch(`/analysis/${project}${qs ? `?${qs}` : ''}`)
  }

  async getAnalysis(project: string, groupId: string): Promise<AnalysisData> {
    return this.fetch(`/analysis/${project}/${groupId}`)
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

  async deleteAnalysis(project: string, groupId: string): Promise<{ message: string }> {
    return this.fetch(`/analysis/${project}/${groupId}`, {
      method: 'DELETE',
    })
  }

  async getFailureCategories(project: string): Promise<FailureCategory[]> {
    return this.fetch(`/categories/${project}`)
  }

  async createFailureCategory(
    project: string,
    category: { name: string; description?: string }
  ): Promise<FailureCategory> {
    return this.fetch(`/categories/${project}`, {
      method: 'POST',
      body: JSON.stringify(category),
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

  async getPlaygroundMetadata(): Promise<PlaygroundMetadata> {
    return this.fetch('/playground/metadata')
  }

  async getAgents(): Promise<{ agents: AgentSummary[] }> {
    return this.fetch('/agents')
  }

  async getAgentMetadata(agentKey: string): Promise<PlaygroundMetadata> {
    return this.fetch(`/agents/${agentKey}/metadata`)
  }

  async updateAgent(agentKey: string, payload: AgentUpdatePayload): Promise<PlaygroundMetadata> {
    return this.fetch(`/agents/${agentKey}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  }

  async createAgent(payload: AgentCreatePayload): Promise<AgentCreateResponse> {
    return this.fetch('/agents', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async deleteAgent(agentKey: string): Promise<AgentDeleteResponse> {
    const encoded = encodeURIComponent(agentKey)
    return this.fetch(`/agents/${encoded}`, {
      method: 'DELETE',
    })
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

  async createPlaygroundSession(payload: PlaygroundSessionPayload): Promise<PlaygroundSessionResponse> {
    return this.fetch('/playground/session', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async createAgentSession(agentKey: string, payload: PlaygroundSessionPayload): Promise<PlaygroundSessionResponse> {
    return this.fetch(`/agents/${agentKey}/session`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  createWebSocket(): WebSocket {
    const wsUrl = this.baseUrl.replace('http', 'ws').replace('/api', '/api/ws/traces')
    return new WebSocket(wsUrl)
  }

  createScenarioRunWebSocket(jobId: string): WebSocket {
    const base = this.baseUrl || '/api'
    let resolved: URL
    if (/^https?:/i.test(base) || /^ws(s)?:/i.test(base)) {
      resolved = new URL(base)
    } else {
      resolved = new URL(base, window.location.origin)
    }
    const wsUrl = new URL(resolved.toString())
    wsUrl.protocol = resolved.protocol === 'https:' ? 'wss:' : resolved.protocol === 'http:' ? 'ws:' : resolved.protocol
    const basePath = resolved.pathname.replace(/\/$/, '') || '/api'
    wsUrl.pathname = `${basePath}/evaluation/scenarios/run/${jobId}/ws`
    const token = resolved.searchParams.get('token')
    wsUrl.search = ''
    if (token) {
      wsUrl.searchParams.set('token', token)
    }
    return new WebSocket(wsUrl.toString())
  }

  createSuiteRunWebSocket(jobId: string): WebSocket {
    const base = this.baseUrl || '/api'
    let resolved: URL
    if (/^https?:/i.test(base) || /^ws(s)?:/i.test(base)) {
      resolved = new URL(base)
    } else {
      resolved = new URL(base, window.location.origin)
    }
    const wsUrl = new URL(resolved.toString())
    wsUrl.protocol = resolved.protocol === 'https:' ? 'wss:' : resolved.protocol === 'http:' ? 'ws:' : resolved.protocol
    const basePath = resolved.pathname.replace(/\/$/, '') || '/api'
    wsUrl.pathname = `${basePath}/evaluation/suite/run/${jobId}/ws`
    const token = resolved.searchParams.get('token')
    wsUrl.search = ''
    if (token) {
      wsUrl.searchParams.set('token', token)
    }
    return new WebSocket(wsUrl.toString())
  }

  createScenarioGenerationWebSocket(jobId: string): WebSocket {
    const base = this.baseUrl || '/api'
    let resolved: URL
    if (/^https?:/i.test(base) || /^ws(s)?:/i.test(base)) {
      resolved = new URL(base)
    } else {
      resolved = new URL(base, window.location.origin)
    }
    const wsUrl = new URL(resolved.toString())
    wsUrl.protocol = resolved.protocol === 'https:' ? 'wss:' : resolved.protocol === 'http:' ? 'ws:' : resolved.protocol
    const basePath = resolved.pathname.replace(/\/$/, '') || '/api'
    wsUrl.pathname = `${basePath}/evaluation/scenarios/generate/${jobId}/ws`
    const token = resolved.searchParams.get('token')
    wsUrl.search = ''
    if (token) {
      wsUrl.searchParams.set('token', token)
    }
    return new WebSocket(wsUrl.toString())
  }

  createJudgeOptimizationWebSocket(jobId: string): WebSocket {
    const base = this.baseUrl || '/api'
    let resolved: URL
    if (/^https?:/i.test(base) || /^ws(s)?:/i.test(base)) {
      resolved = new URL(base)
    } else {
      resolved = new URL(base, window.location.origin)
    }
    const wsUrl = new URL(resolved.toString())
    wsUrl.protocol = resolved.protocol === 'https:' ? 'wss:' : resolved.protocol === 'http:' ? 'ws:' : resolved.protocol
    const basePath = resolved.pathname.replace(/\/$/, '') || '/api'
    wsUrl.pathname = `${basePath}/evaluation/judges/optimize/${jobId}/ws`
    const token = resolved.searchParams.get('token')
    wsUrl.search = ''
    if (token) {
      wsUrl.searchParams.set('token', token)
    }
    return new WebSocket(wsUrl.toString())
  }

  createAgentOptimizationWebSocket(jobId: string): WebSocket {
    const base = this.baseUrl || '/api'
    let resolved: URL
    if (/^https?:/i.test(base) || /^ws(s)?:/i.test(base)) {
      resolved = new URL(base)
    } else {
      resolved = new URL(base, window.location.origin)
    }
    const wsUrl = new URL(resolved.toString())
    wsUrl.protocol = resolved.protocol === 'https:' ? 'wss:' : resolved.protocol === 'http:' ? 'ws:' : resolved.protocol
    const basePath = resolved.pathname.replace(/\/$/, '') || '/api'
    wsUrl.pathname = `${basePath}/evaluation/agents/optimize/${jobId}/ws`
    const token = resolved.searchParams.get('token')
    wsUrl.search = ''
    if (token) {
      wsUrl.searchParams.set('token', token)
    }
    return new WebSocket(wsUrl.toString())
  }

  createPlaygroundWebSocket(sessionId: string, explicitBase?: string, agentKey?: string): WebSocket {
    if (explicitBase) {
      const resolved = new URL(explicitBase)
      resolved.searchParams.set('session_id', sessionId)
      if (agentKey) {
        resolved.searchParams.set('agent', agentKey)
      }
      const urlStr = resolved.toString()
      if (typeof window !== 'undefined' && window.console) {
        console.debug('[playground] websocket url', urlStr)
      }
      return new WebSocket(urlStr)
    }

    const base = this.baseUrl || '/api'
    let resolved: URL
    if (/^https?:/i.test(base) || /^ws(s)?:/i.test(base)) {
      resolved = new URL(base)
    } else {
      resolved = new URL(base, window.location.origin)
    }

    const wsUrl = new URL(resolved.toString())
    wsUrl.protocol = resolved.protocol === 'https:' ? 'wss:' : resolved.protocol === 'http:' ? 'ws:' : resolved.protocol

    const basePath = resolved.pathname.replace(/\/$/, '') || '/api'
    wsUrl.pathname = `${basePath}/playground/ws`

    const token = resolved.searchParams.get('token')
    wsUrl.search = ''
    if (token) {
      wsUrl.searchParams.set('token', token)
    }
    wsUrl.searchParams.set('session_id', sessionId)
    if (agentKey) {
      wsUrl.searchParams.set('agent', agentKey)
    }
    const urlString = wsUrl.toString()
    if (typeof window !== 'undefined' && window.console) {
      console.debug('[playground] websocket url', urlString)
    }
    return new WebSocket(urlString)
  }
}

export const api = new ApiClient()
export default api
