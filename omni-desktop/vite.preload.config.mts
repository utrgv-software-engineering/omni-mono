import { resolve } from 'path';
import { defineConfig } from 'vite';
import tsconfigPaths from 'vite-tsconfig-paths';

// https://vitejs.dev/config
export default defineConfig({
  plugins: [tsconfigPaths()],
  build: {
    lib: {
      entry: resolve(__dirname, 'src/preload/index.ts'),
      formats: ['cjs'],
      name: 'preload',
    },
    outDir: '.vite/build',
    rollupOptions: {
      output: {
        entryFileNames: 'preload.js',
      },
      external: ['electron', 'node-pty'],
    },
  },
});
