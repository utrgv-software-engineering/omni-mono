import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: 'jsdom',
      passWithNoTests: true,
      coverage: {
        reporter: ['text', 'lcov'],
        reportsDirectory: '../../../../coverage/web',
        include: ['src/**/*.{ts,tsx}'],
        exclude: ['src/**/*.d.ts'],
      },
    },
  })
)
