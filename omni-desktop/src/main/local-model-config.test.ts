import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { ModelsConfig } from '@/shared/types';

import { readLocalModels, writeLocalModels } from './local-model-config';

const config = (provider: string): ModelsConfig => ({
  version: 3,
  default: `${provider}/a`,
  voice_default: null,
  providers: { [provider]: { type: 'openai', models: { a: { model: 'a' } } } },
});

describe('shared models.json', () => {
  it('creates an empty file once and reads CLI changes back', () => {
    const directory = mkdtempSync(join(tmpdir(), 'local-models-'));
    expect(readLocalModels(directory)).toEqual({ version: 3, default: null, voice_default: null, providers: {} });
    writeLocalModels(directory, config('cli'));
    expect(readLocalModels(directory).default).toBe('cli/a');
  });

  it('does not replace a corrupt file', () => {
    const directory = mkdtempSync(join(tmpdir(), 'local-models-'));
    writeFileSync(join(directory, 'models.json'), '{invalid');
    expect(() => readLocalModels(directory)).toThrow();
    expect(readFileSync(join(directory, 'models.json'), 'utf8')).toBe('{invalid');
  });
});
