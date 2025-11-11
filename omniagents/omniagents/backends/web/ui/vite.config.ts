import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const wsHost = process.env.OMNI_WS_HOST || '127.0.0.1'
const wsPort = Number(process.env.OMNI_WS_PORT || 8000)

export default defineConfig({
  plugins: [react()],
  build: {
    sourcemap: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    proxy: {
      '/ws': {
        target: `http://${wsHost}:${wsPort}`,
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
