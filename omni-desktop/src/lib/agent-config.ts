/**
 * Pure helpers for the agent's runtime config (models / env).
 *
 * No fs, Electron, or node:crypto imports — safe for the renderer, main, and
 * server bundles alike.
 */
import { modelProvider } from '@omniagents/client/rpc/model-catalog';

import type { ModelsConfig } from '@/shared/types';

/** Empty `models.json` (current schema version). */
export function emptyModelsConfig(): ModelsConfig {
  return { version: 3, default: null, voice_default: null, providers: {} };
}

/**
 * Parse raw `.env` text into a `{ KEY: value }` map for direct injection into
 * the agent process env. Mirrors the
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

/** Models never carry their own connection: the provider's applies. Drop any
 * per-model connection fields before saving. */
export function stripModelConnectionFields(config: ModelsConfig): ModelsConfig {
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

export function validateModelsConfig(config: ModelsConfig): void {
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
