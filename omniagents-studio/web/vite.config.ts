import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const studio = process.env.STUDIO_API_URL || 'http://localhost:7860'

export default defineConfig({
  plugins: [tailwindcss(), react()],
  // streamdown (the chat UI's markdown) lazy-loads inner chunks; optimizing
  // it up front keeps chunk hashes stable for an open page.
  optimizeDeps: {
    include: ['streamdown', '@streamdown/code', '@streamdown/math', '@streamdown/mermaid', '@streamdown/cjk'],
  },
  build: {
    chunkSizeWarningLimit: 4000,
  },
  server: {
    // Every Studio route, including each agent runtime's sockets, is under /api.
    proxy: {
      '/api': { target: studio, changeOrigin: true, ws: true },
    },
  },
})
