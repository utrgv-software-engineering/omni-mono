/**
 * Typed contract for the GUI protocol's model & capability catalog RPCs
 * (`list_models` / `get_model` / `list_providers` / `set_session_model` /
 * `set_session_reasoning` / `set_voice_model`).
 *
 * Mirrors omniagents/core/models/catalog_api.py and protocol.md
 * § "Model & Capability Catalog". Kept identical between the web and Ink
 * reference clients — both consume the same catalog contract.
 */

/** A typed, actionable reason a model/operation cannot be served. */
export type CatalogReason = {
  /**
   * Stable machine-readable code. Current vocabulary:
   * `no_model_store` | `unknown_model` | `oauth_signin_required` |
   * `missing_credentials` | `missing_base_url` | `realtime_only` |
   * `not_realtime` | `invalid_reasoning_effort` | `voice_default_rejected`.
   */
  code: string
  message: string
  /** Human-actionable remediation, when one exists. */
  action?: string
}

/** A per-provider failure surfaced without hiding other providers. */
export type CatalogProviderError = {
  provider: string
  code: 'provider_error'
  message: string
}

export type ModelDescriptor = {
  /** Canonical catalog identity — the normalized model ref. */
  id: string
  /** Provider-side model id (deployment name, slug, ...). */
  model: string | null
  label: string
  description: string | null
  provider: { name: string | null; type: string | null }
  modalities: string[]
  realtime: boolean
  limits: { max_input_tokens: number | null; max_output_tokens: number | null }
  reasoning: { default: string | null; options: string[] }
  tiers: { service: string | null; speed: string | null }
  personality: { supported: boolean; options: string[]; default: string | null }
  availability: { available: boolean; reasons: CatalogReason[] }
  entitlement: {
    entitled: boolean
    credential: 'platform' | 'oauth' | 'api_key' | 'env' | 'none'
  }
  deprecation: { deprecated: boolean; message: string | null; replace_with: string | null }
  hidden: boolean
  is_default: boolean
  is_voice_default: boolean
  is_user_defined: boolean
}

export type ProviderDescriptor = {
  name: string
  type: string
  base_url: string | null
  is_default_provider: boolean
  is_user_defined: boolean
  model_count: number
  hidden_model_count: number
  capabilities: { realtime: boolean; reasoning: boolean; modalities: string[] }
  health: { status: 'ok' | 'needs_auth' | 'error'; detail: string | null }
}

export type ListModelsResult = {
  models: ModelDescriptor[]
  default_model: string | null
  voice_default_model: string | null
  errors: CatalogProviderError[]
  reasons: CatalogReason[]
  session?: {
    session_id: string
    active_model: string | null
    reasoning_effort: string | null
  }
}

export type GetModelResult = {
  found: boolean
  model: ModelDescriptor | null
  reasons: CatalogReason[]
}

export type ListProvidersResult = {
  providers: ProviderDescriptor[]
  errors: CatalogProviderError[]
  reasons: CatalogReason[]
}

export type SetSessionModelResult = {
  ok: boolean
  session_id?: string
  model?: string
  label?: string
  provider?: string
  max_input_tokens?: number | null
  max_output_tokens?: number | null
  reasoning_effort?: string | null
  /** Availability reasons for a successful switch to an unentitled model. */
  warnings?: CatalogReason[]
  reasons?: CatalogReason[]
}

export type SetSessionReasoningResult = {
  ok: boolean
  session_id?: string
  reasoning_effort?: string
  model?: string | null
  reasons?: CatalogReason[]
}

export type SetVoiceModelResult = {
  ok: boolean
  model?: string
  label?: string
  provider?: string
  reasons?: CatalogReason[]
}
