import { atom } from 'nanostores';

import { emitter } from '@/renderer/services/ipc';
import type { McpConfig, ModelsConfig } from '@/shared/types';

export const configApi = {
  getOmniConfigDir: () => emitter.invoke('config:get-omni-config-dir'),
  getEnvFilePath: () => emitter.invoke('config:get-env-file-path'),
  readJsonFile: (path: string) => emitter.invoke('config:read-json-file', path),
  writeJsonFile: (path: string, data: unknown) => emitter.invoke('config:write-json-file', path, data),
  readTextFile: (path: string) => emitter.invoke('config:read-text-file', path),
  writeTextFile: (path: string, content: string) => emitter.invoke('config:write-text-file', path, content),
};

/**
 * The shared `models.json`, mirrored for renderer reads that can't await IPC
 * (e.g. whether hosted voice is configured). Loaded at boot and refreshed on
 * every save through {@link agentConfigApi.setModels}.
 */
export const $modelsConfig = atom<ModelsConfig | null>(null);

export const refreshModelsConfig = async (): Promise<ModelsConfig> => {
  const config = await emitter.invoke('settings:get-models-config');
  $modelsConfig.set(config);
  return config;
};

/** Typed agent-config API; the backend persists each value. */
export const agentConfigApi = {
  getModels: () => refreshModelsConfig(),
  setModels: async (
    config: ModelsConfig,
    options?: { expectedProvider: string | null; confirmProviderChange?: boolean }
  ) => {
    await emitter.invoke('settings:set-models-config', config, options);
    await refreshModelsConfig();
  },
  getMcp: () => emitter.invoke('settings:get-mcp-config'),
  setMcp: (config: McpConfig) => emitter.invoke('settings:set-mcp-config', config),
  getEnv: () => emitter.invoke('settings:get-env'),
  setEnv: (content: string) => emitter.invoke('settings:set-env', content),
};
