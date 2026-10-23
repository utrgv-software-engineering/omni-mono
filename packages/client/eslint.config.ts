import js from '@eslint/js';
import typescriptEslint from '@typescript-eslint/eslint-plugin';
import typescriptParser from '@typescript-eslint/parser';
import pluginImport from 'eslint-plugin-import';
import pluginSimpleImportSort from 'eslint-plugin-simple-import-sort';
import pluginUnusedImports from 'eslint-plugin-unused-imports';
import globals from 'globals';

export default [
  js.configs.recommended,
  {
    files: ['**/*.ts'],
    languageOptions: {
      parser: typescriptParser,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      '@typescript-eslint': typescriptEslint,
      import: pluginImport,
      'unused-imports': pluginUnusedImports,
      'simple-import-sort': pluginSimpleImportSort,
    },
    rules: {
      ...typescriptEslint.configs.recommended?.rules,
      curly: 'error',
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'no-undef': 'off',
      'no-redeclare': 'off',
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-throw-literal': 'error',
      'no-promise-executor-return': 'error',
      'prefer-template': 'error',
      'import/no-duplicates': 'error',
      'unused-imports/no-unused-imports': 'error',
      'unused-imports/no-unused-vars': [
        'error',
        { vars: 'all', varsIgnorePattern: '^_', args: 'after-used', argsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/no-unused-vars': 'off',
      'simple-import-sort/imports': 'error',
      'simple-import-sort/exports': 'error',
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'separate-type-imports', disallowTypeAnnotations: false },
      ],
      '@typescript-eslint/no-import-type-side-effects': 'error',
    },
  },
  {
    files: ['**/*.test.ts'],
    rules: {
      'no-promise-executor-return': 'off',
      'unused-imports/no-unused-vars': 'off',
    },
  },
  {
    // Protocol payloads reach the client untyped; the desktop held this code
    // to the same rule.
    files: ['src/**/*.ts'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
  {
    // Generated from the OpenRPC documents by omniagents' protocol generator.
    ignores: ['src/protocol/gui-v*.ts', 'src/protocol/schema-types*.ts'],
  },
];
