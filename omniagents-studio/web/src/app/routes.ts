export const ROUTES = {
  home: '/',
  logs: '/logs',
  playground: '/playground',
  agents: '/agents',
  agentDetail: '/agents/:agentKey',
  evaluation: '/evaluation',
} as const

export const QUERY_PARAMS = {
  project: 'project',
  workflow: 'workflow',
  conversation: 'conversation',
  trace: 'trace',
  mode: 'mode',
  tab: 'tab',
  session: 'session',
} as const

export type RouteKey = keyof typeof ROUTES

export type QueryParamKey = keyof typeof QUERY_PARAMS
