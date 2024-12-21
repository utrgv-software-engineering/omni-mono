import { resolve } from 'path';
import { defineConfig } from 'vite';
import tsconfigPaths from 'vite-tsconfig-paths';

// https://vitejs.dev/config
export default defineConfig({
  plugins: [tsconfigPaths()],
  build: {
    lib: {
      entry: resolve(__dirname, 'src/main/index.ts'),
      formats: ['cjs'],
      name: 'main',
    },
    outDir: '.vite/build',
    rollupOptions: {
      output: {
        entryFileNames: 'main.js',
      },
      external: ['electron', 'node-pty'],
    },
  },
});
