/// <reference types="vitest" />
import react from '@vitejs/plugin-react-swc';
import { defineConfig } from 'vite';
import { createHtmlPlugin } from 'vite-plugin-html';
import tsconfigPaths from 'vite-tsconfig-paths';

export default defineConfig({
  plugins: [
    react(),
    tsconfigPaths(),
    createHtmlPlugin({
      // index.dev.html has react devtools
      template: process.env.NODE_ENV === 'development' ? './index.dev.html' : './index.html',
    }),
  ],
  build: {
    rollupOptions: {
      external: ['electron', 'node-pty'],
    },
  },
});
