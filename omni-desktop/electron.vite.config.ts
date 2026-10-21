import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react-swc';
import { defineConfig } from 'electron-vite';
import { resolve } from 'path';
import { searchForWorkspaceRoot } from 'vite';
import { createHtmlPlugin } from 'vite-plugin-html';
import tsconfigPaths from 'vite-tsconfig-paths';

export default defineConfig({
  main: {
    plugins: [tsconfigPaths()],
    build: {
      lib: {
        entry: resolve('src/main/index.ts'),
      },
      rollupOptions: {
        // `ws` has optional native deps (`bufferutil`, `utf-8-validate`) that
        // it requires inside a try/catch and falls back to pure JS when
        // missing. Rollup tries to statically resolve them during bundling
        // and errors out if they're not installed. Marking them external
        // lets the runtime `require` fail gracefully into the JS fallback,
        // which is exactly what `ws` is designed for.
        external: ['node-pty', 'bufferutil', 'utf-8-validate'],
      },
    },
  },
  preload: {
    plugins: [tsconfigPaths()],
    build: {
      lib: {
        entry: resolve('src/preload/index.ts'),
      },
    },
  },
  renderer: {
    root: '.',
    server: {
      // The GUI protocol module is imported from omniagents (tsconfig paths).
      fs: {
        allow: [
          searchForWorkspaceRoot(process.cwd()),
          resolve('../omniagents/omniagents/backends/web/ui/src/protocol/generated'),
        ],
      },
    },
    // Pre-bundle streamdown + its plugins at server start. They lazy-load
    // inner chunks (e.g. streamdown's `highlighted-body`), so if Vite
    // discovers them on first markdown render it re-optimizes mid-session,
    // changing chunk hashes and 404-ing any already-open page with "Failed to
    // fetch dynamically imported module". Forcing them into the initial
    // optimize pass keeps the hashes stable.
    optimizeDeps: {
      include: ['streamdown', '@streamdown/code', '@streamdown/math', '@streamdown/mermaid', '@streamdown/cjk'],
    },
    plugins: [
      tailwindcss(),
      react(),
      tsconfigPaths(),
      createHtmlPlugin({
        // index.dev.html has react devtools
        template: process.env.NODE_ENV === 'development' ? './index.dev.html' : './index.html',
        inject: {
          data: {
            // Dev loads the renderer from the Vite HMR server (http + ws), so
            // script-src must allow it. Prod is otherwise locked to 'self'.
            // 'wasm-unsafe-eval' permits WebAssembly compilation only (not
            // string eval): the ContextEditor's code blocks highlight with
            // Shiki's Oniguruma WASM engine.
            cspScriptSrc:
              process.env.NODE_ENV === 'development'
                ? "'self' 'unsafe-inline' 'wasm-unsafe-eval' http://localhost:5173"
                : "'self' 'wasm-unsafe-eval'",
          },
        },
      }),
    ],
    build: {
      rollupOptions: {
        input: {
          index: resolve('./index.html'),
        },
      },
    },
  },
});
