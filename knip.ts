import type { KnipConfig } from 'knip';

// Knip runs from the monorepo root, where the workspaces' packages and
// binaries are installed.
const config = {
  workspaces: {
    'omni-desktop': {
      entry: [
        'electron.vite.config.ts',
        'vite.browser.config.ts',
        'vite.server.config.ts',
        'src/main/index.ts',
        'src/preload/index.ts',
        'src/renderer/index.ts',
        'src/server/index.ts',
        'tests/e2e/**/*.ts',
        'scripts/**/*.{js,mjs,ts}',
      ],
      project: ['*.{ts,tsx,js,mjs}', 'src/**/*.{ts,tsx}', 'tests/e2e/**/*.{ts,tsx}', 'scripts/**/*.{js,mjs,ts}'],
      ignoreDependencies: [
        // Optional/runtime provider packages and UI dependencies kept for feature-gated code paths.
        '@fastify/http-proxy',
        '@radix-ui/react-collapsible',
        '@radix-ui/react-dropdown-menu',
        '@radix-ui/react-hover-card',
        '@radix-ui/react-scroll-area',
        '@radix-ui/react-select',
        '@radix-ui/react-separator',
        '@radix-ui/react-slot',
        '@renovatebot/pep440',
        // Recognized by Vite/Tailwind CSS plugins or package manager metadata rather than static imports.
        '@tailwindcss/typography',
        'linkify-react',
        'linkifyjs',
        'react-pdf',
        'tailwindcss',
        'typescript-eslint',
        'vite-plugin-eslint',
      ],
      ignoreBinaries: [
        // This is included with @electron/forge
        'electron-rebuild',
      ],
      ignore: ['electron-builder.config.ts', 'src/server/electron-store-shim.ts'],
      paths: {
        'assets/*': ['assets/*'],
      },
    },
  },
  rules: {
    duplicates: 'off',
    exports: 'off',
    types: 'off',
  },
} satisfies KnipConfig;

export default config;
