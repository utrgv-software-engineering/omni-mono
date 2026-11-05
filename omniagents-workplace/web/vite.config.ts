import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const workplace = process.env.WORKPLACE_URL || 'http://localhost:7300'

export default defineConfig({
  test: { environmentMatchGlobs: [['src/EgressSettings.test.tsx', 'jsdom'], ['src/teams.test.ts', 'jsdom']] },
  plugins: [tailwindcss(), react()],
  build: { rollupOptions: { input: { main: 'index.html', extension: 'extension.html' } } },
  // streamdown (the chat UI's markdown) lazy-loads inner chunks; optimizing
  // it up front keeps chunk hashes stable for an open page (as Studio does).
  optimizeDeps: {
    include: ['streamdown', '@streamdown/code', '@streamdown/math', '@streamdown/mermaid', '@streamdown/cjk'],
  },
  server: {
    proxy: {
      '/workplace': { target: workplace, changeOrigin: true, ws: true },
      '/library/view': { target: workplace, changeOrigin: true },
      '/canvas/view': { target: workplace, changeOrigin: true },
      '/slack': { target: workplace, changeOrigin: true, ws: true },
    },
  },
})
