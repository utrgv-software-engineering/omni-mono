import { describe, expect, it } from 'vitest';

import {
  emptyModelsConfig,
  parseEnvVars,
  PROVIDER_CONNECTION_FIELDS,
  stripModelConnectionFields,
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

describe('empty config factories', () => {
  it('produce schema-valid empties', () => {
    expect(emptyModelsConfig()).toEqual({ version: 3, default: null, voice_default: null, providers: {} });
  });
});

describe('provider-owned connections', () => {
  it('strips per-model connection fields and keeps everything else', () => {
    for (const field of PROVIDER_CONNECTION_FIELDS) {
      const config = emptyModelsConfig();
      config.providers.company = {
        type: 'azure',
        api_key: 'provider-key',
        models: { deployment: { model: 'deployment', [field]: 'private-secret' } },
      };
      const stripped = stripModelConnectionFields(config);
      expect(stripped.providers.company!.models.deployment).toEqual({ model: 'deployment' });
      expect(stripped.providers.company!.api_key).toBe('provider-key');
    }
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
