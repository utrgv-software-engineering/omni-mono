import { randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import type { ModelsConfig } from '@/shared/types';

/** Local desktop and CLI share this file. The settings store is only a migration seed. */
export function readLocalModels(configDir: string, seed: ModelsConfig): ModelsConfig {
  const path = join(configDir, 'models.json');
  if (!existsSync(path)) {
    writeLocalModels(configDir, seed);
  }
  const config = JSON.parse(readFileSync(path, 'utf8')) as ModelsConfig;
  if (config.version !== 3 || !config.providers || typeof config.providers !== 'object') {
    throw new Error('Invalid models.json. Repair the model configuration before continuing.');
  }
  return config;
}

export function writeLocalModels(configDir: string, config: ModelsConfig): void {
  mkdirSync(configDir, { recursive: true });
  const temporary = join(configDir, `.models-${randomUUID()}.tmp`);
  try {
    writeFileSync(temporary, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
    renameSync(temporary, join(configDir, 'models.json'));
  } finally {
    if (existsSync(temporary)) {
      unlinkSync(temporary);
    }
  }
}
