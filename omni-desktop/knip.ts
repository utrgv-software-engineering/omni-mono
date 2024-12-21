import type { KnipConfig } from 'knip';

const config: KnipConfig = {
  entry: ['src/main/index.ts', 'src/renderer/index.ts', 'src/preload/index.ts', 'vite.*.ts'],
  project: ['src/**/*.{ts,tsx}!'],
  // TODO(psyche): these deps are somehow not recognized by knip so we need to explicitly ignore them
  ignoreDependencies: [
    '@electron-forge/plugin-auto-unpack-natives',
    '@electron-forge/plugin-fuses',
    '@electron-forge/plugin-vite',
    '@electron/fuses',
    '@typescript-eslint/eslint-plugin',
    '@typescript-eslint/parser',
    '@vitejs/plugin-react',
    '@vitejs/plugin-react-swc',
    'eslint-plugin-import',
    'eslint-plugin-react-hooks',
    'eslint-plugin-react-refresh',
    'eslint-plugin-unused-imports',
    'globals',
    'ts-node',
    'typescript-eslint',
    'vite-plugin-eslint',
    'vite-tsconfig-paths',
    'vite-plugin-html',
  ],
  ignoreBinaries: [
    // This is included with @electron/forge
    'electron-rebuild',
  ],
  ignore: ['forge.*.ts'],
  paths: {
    'assets/*': ['assets/*'],
  },
};

export default config;
