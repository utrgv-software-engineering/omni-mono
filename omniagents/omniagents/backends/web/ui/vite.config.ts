import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react-swc';
import { defineConfig } from 'vite';

const wsHost = process.env.OMNI_WS_HOST || '127.0.0.1';
const wsPort = Number(process.env.OMNI_WS_PORT || 8000);

export default defineConfig({
  plugins: [tailwindcss(), react()],
  // streamdown lazy-loads inner chunks; optimizing it up front keeps chunk
  // hashes stable for an open page.
  optimizeDeps: {
    include: ['streamdown', '@streamdown/code', '@streamdown/math', '@streamdown/mermaid', '@streamdown/cjk'],
  },
  build: {
    sourcemap: true,
    chunkSizeWarningLimit: 4000,
  },
  server: {
    proxy: {
      '/ws': { target: `http://${wsHost}:${wsPort}`, ws: true, changeOrigin: true },
      '/api': { target: `http://${wsHost}:${wsPort}`, changeOrigin: true },
    },
  },
});
