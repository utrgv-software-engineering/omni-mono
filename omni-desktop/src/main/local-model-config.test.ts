import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { ModelsConfig } from '@/shared/types';

import { materializeAgentConfig } from './config-materializer';
import { readLocalModels, writeLocalModels } from './local-model-config';

const config = (provider: string): ModelsConfig => ({
  version: 3,
  default: `${provider}/a`,
  voice_default: null,
  providers: { [provider]: { type: 'openai', models: { a: { model: 'a' } } } },
});

describe('local model ownership', () => {
  it('seeds once and preserves CLI changes across startup and unrelated settings writes', () => {
    const directory = mkdtempSync(join(tmpdir(), 'local-models-'));
    expect(readLocalModels(directory, config('old')).default).toBe('old/a');
    writeLocalModels(directory, config('cli'));
    expect(readLocalModels(directory, config('old')).default).toBe('cli/a');
    materializeAgentConfig({
      configDir: directory,
      mcp: { mcpServers: {} },
    });
    expect(JSON.parse(readFileSync(join(directory, 'models.json'), 'utf8')).default).toBe('cli/a');
    writeLocalModels(directory, config('desktop'));
    expect(readLocalModels(directory, config('old')).default).toBe('desktop/a');
  });

  it('does not replace corrupt external configuration with stale settings', () => {
    const directory = mkdtempSync(join(tmpdir(), 'local-models-'));
    writeFileSync(join(directory, 'models.json'), '{invalid');
    expect(() => readLocalModels(directory, config('old'))).toThrow();
    expect(readFileSync(join(directory, 'models.json'), 'utf8')).toBe('{invalid');
  });
});
