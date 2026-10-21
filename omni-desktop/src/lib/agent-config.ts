/**
 * Pure helpers for the agent's runtime config (models / MCP / env).
 *
 * No fs, Electron, or node:crypto imports — safe for the renderer, main, and
 * server bundles alike. The secret-rewrite logic that DOES need hashing lives
 * in `src/main/config-materializer.ts` (main/server only); this module holds
 * the default shapes, the `.env` parser, and the env-ref predicate they share.
 */
import type { McpConfig, ModelsConfig } from '@/shared/types';

/** Empty `models.json` (current schema version). */
export function emptyModelsConfig(): ModelsConfig {
  return { version: 3, default: null, voice_default: null, providers: {} };
}

/** Empty `mcp.json`. */
export function emptyMcpConfig(): McpConfig {
  return { mcpServers: {} };
}

/**
 * Matches a `${VAR}` or `${VAR:-default}` reference anywhere in a string —
 * the same grammar both omni-code (`models.py`) and omniagents (`user_mcp.py`)
 * expand at load time. Used to detect user-authored refs so the materializer
 * never double-rewrites them into launcher secrets.
 */
const ENV_REF_RE = /\$\{[^}:]+(?::-[^}]*)?\}/;

/** True when `value` already contains a `${...}` reference (user-supplied). */
export function containsEnvRef(value: string): boolean {
  return ENV_REF_RE.test(value);
}

/**
 * Parse raw `.env` text into a `{ KEY: value }` map for direct injection into
 * the agent process env (cloud, where no `.env` file is written). Mirrors the
 * structure the Environment tab round-trips: `#` comments and blank lines are
 * skipped, the first `=` splits key from value, surrounding whitespace on the
 * key is trimmed, the value is taken verbatim (no quote stripping — the agent
 * env should receive exactly what the user typed).
 */
export function parseEnvVars(content: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of content.split('\n')) {
    const trimmed = raw.trim();
    if (trimmed === '' || trimmed.startsWith('#')) {
      continue;
    }
    const eq = raw.indexOf('=');
    if (eq === -1) {
      continue;
    }
    const key = raw.slice(0, eq).trim();
    if (key === '') {
      continue;
    }
    out[key] = raw.slice(eq + 1);
  }
  return out;
}

/** Provider identity is the catalog key, not the transport type (e.g. azure). */
export function modelProvider(ref: string | null | undefined): string | null {
  if (!ref) {
    return null;
  }
  const slash = ref.indexOf('/');
  return slash < 0 ? 'openai' : ref.slice(0, slash);
}

export const PROVIDER_CONNECTION_FIELDS = [
  'api_key',
  'base_url',
  'api_version',
  'credential_ref',
  'routing_prefix',
  'realtime_url',
  'realtime_base_url',
  'websocket_base_url',
  'organization',
  'project',
  'provider',
  'provider_name',
  'type',
  'use_responses',
  'use_responses_websocket',
] as const;

export function modelConnectionIssues(config: ModelsConfig): string[] {
  const issues: string[] = [];
  for (const [provider, entry] of Object.entries(config.providers)) {
    for (const [id, model] of Object.entries(entry.models)) {
      const settings = model.model_settings ?? {};
      const connectionSettings = ['extra_headers', 'extra_query', 'extra_args'].filter((key) =>
        Object.hasOwn(settings, key)
      );
      if (connectionSettings.length) {
        issues.push(`${provider}/${id}: model_settings.${connectionSettings.join(', model_settings.')}`);
      }
      const fields = PROVIDER_CONNECTION_FIELDS.filter((field) => Object.hasOwn(model, field));
      if (fields.length) {
        issues.push(`${provider}/${id}: ${fields.join(', ')}`);
      }
    }
  }
  return issues;
}

export function validateModelsConfig(config: ModelsConfig): void {
  const issues = modelConnectionIssues(config);
  if (issues.length) {
    throw new Error(
      `Models contain provider-only connection fields: ${issues.join('; ')}. Move these fields to the provider or create a separate provider. Existing settings have not been changed.`
    );
  }
  const selected = config.providers[modelProvider(config.default) ?? ''];
  if (selected?.type === 'litellm') {
    const prefix = selected.routing_prefix;
    if (!prefix || !/^[a-z][a-z0-9_-]*$/.test(prefix)) {
      throw new Error('Set the provider Routing prefix in Advanced (for example, anthropic) before using LiteLLM.');
    }
    if (
      Object.values(selected.models).some(
        (model) => !model.model.startsWith(`${prefix}/`) || model.model_settings?.extra_body
      )
    ) {
      throw new Error('LiteLLM models must use the provider Routing prefix and cannot supply extra request bodies.');
    }
  }
  if (config.voice_default && modelProvider(config.voice_default) !== modelProvider(config.default)) {
    throw new Error(
      'Hosted voice must use the selected workspace provider. Choose a voice model from that provider or turn hosted voice off.'
    );
  }
}

/** Explicit migration: caller confirms the change of connection before using it. */
export function useProviderConnections(config: ModelsConfig): ModelsConfig {
  return {
    ...config,
    providers: Object.fromEntries(
      Object.entries(config.providers).map(([name, provider]) => [
        name,
        {
          ...provider,
          models: Object.fromEntries(
            Object.entries(provider.models).map(([id, model]) => {
              const next = Object.fromEntries(
                Object.entries(model).filter(
                  ([key]) => !(PROVIDER_CONNECTION_FIELDS as readonly string[]).includes(key)
                )
              ) as typeof model;
              if (next.model_settings) {
                next.model_settings = Object.fromEntries(
                  Object.entries(next.model_settings).filter(
                    ([key]) => !['extra_headers', 'extra_query', 'extra_args'].includes(key)
                  )
                );
              }
              return [id, next];
            })
          ),
        },
      ])
    ),
  };
}
