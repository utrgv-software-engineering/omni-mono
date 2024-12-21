import { defineConfig, mergeConfig } from 'vitest/config';

import viteConfig from './vite.renderer.config.mjs';

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: 'jsdom',
      typecheck: {
        enabled: true,
        ignoreSourceErrors: true,
      },
      coverage: {
        provider: 'v8',
        all: false,
        reporter: ['html'],
      },
    },
  })
);
