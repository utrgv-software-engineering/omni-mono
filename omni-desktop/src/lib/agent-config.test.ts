import { describe, expect, it } from 'vitest';

import {
  containsEnvRef,
  emptyMcpConfig,
  emptyModelsConfig,
  emptyNetworkConfig,
  parseEnvVars,
  PROVIDER_CONNECTION_FIELDS,
  useProviderConnections,
  validateModelsConfig,
} from '@/lib/agent-config';

describe('parseEnvVars', () => {
  it('parses KEY=value, skipping comments and blanks', () => {
    expect(parseEnvVars('# comment\n\nFOO=bar\nBAZ=qux')).toEqual({ FOO: 'bar', BAZ: 'qux' });
  });

  it('keeps everything after the first = (values may contain =)', () => {
    expect(parseEnvVars('URL=https://x?a=1&b=2')).toEqual({ URL: 'https://x?a=1&b=2' });
  });

  it('trims the key but not the value, and skips keyless lines', () => {
    expect(parseEnvVars('  KEY =  spaced \nnoequalshere\n=novalue')).toEqual({ KEY: '  spaced ' });
  });
});

describe('containsEnvRef', () => {
  it('detects ${VAR} and ${VAR:-default}', () => {
    expect(containsEnvRef('${OPENAI_KEY}')).toBe(true);
    expect(containsEnvRef('Bearer ${TOK:-none}')).toBe(true);
  });
  it('is false for plain values', () => {
    expect(containsEnvRef('sk-abc123')).toBe(false);
  });
});

describe('empty config factories', () => {
  it('produce schema-valid empties', () => {
    expect(emptyModelsConfig()).toEqual({ version: 3, default: null, voice_default: null, providers: {} });
    expect(emptyMcpConfig()).toEqual({ mcpServers: {} });
    expect(emptyNetworkConfig().enabled).toBe(false);
  });
});

describe('provider-owned connections', () => {
  it('rejects model connection overrides without exposing their values', () => {
    for (const field of PROVIDER_CONNECTION_FIELDS) {
      const config = emptyModelsConfig();
      config.providers.company = {
        type: 'azure',
        models: { deployment: { model: 'deployment', [field]: 'private-secret' } },
      };
      expect(() => validateModelsConfig(config)).toThrow('provider-only');
      expect(() => validateModelsConfig(config)).not.toThrow('private-secret');
      expect(config.providers.company.models.deployment).toHaveProperty(field, 'private-secret');
    }
  });
  it('requires explicit migration and preserves provider connections', () => {
    const config = emptyModelsConfig();
    config.providers.company = {
      type: 'azure',
      api_key: 'company-key',
      base_url: 'https://company.invalid',
      models: {
        deployment: {
          model: 'deployment',
          model_settings: { extra_headers: { Authorization: 'legacy' }, temperature: 0.4 },
        },
      },
    };
    const migrated = useProviderConnections(config);
    expect(() => validateModelsConfig(config)).toThrow('provider-only');
    expect(() => validateModelsConfig(migrated)).not.toThrow();
    expect(migrated.providers.company?.api_key).toBe('company-key');
    expect(migrated.providers.company?.models.deployment?.model_settings).toEqual({ temperature: 0.4 });
  });
  it('rejects hosted voice from a different provider', () => {
    expect(() =>
      validateModelsConfig({ ...emptyModelsConfig(), default: 'company/chat', voice_default: 'personal/voice' })
    ).toThrow('Hosted voice');
  });
});

it('keeps LiteLLM vendor routing on the provider', () => {
  const config = {
    version: 3 as const,
    default: 'company/claude',
    voice_default: null,
    providers: {
      company: {
        type: 'litellm' as const,
        api_key: 'provider-key',
        routing_prefix: 'anthropic',
        models: { claude: { model: 'anthropic/claude-example' } },
      },
    },
  };
  expect(() => validateModelsConfig(config)).not.toThrow();
  config.providers.company.models.claude.model = 'openai/gpt-example';
  expect(() => validateModelsConfig(config)).toThrow('Routing prefix');
});
