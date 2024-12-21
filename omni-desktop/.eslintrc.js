module.exports = {
  extends: ['@invoke-ai/eslint-config-react'],
  plugins: ['path'],
  rules: {
    // TODO(psyche): Enable this rule. Requires no default exports in components - many changes.
    'react-refresh/only-export-components': 'off',
    // TODO(psyche): Enable this rule. Requires a lot of eslint-disable-next-line comments.
    '@typescript-eslint/consistent-type-assertions': 'off',
    // https://eslint.org/docs/latest/rules/no-promise-executor-return
    'no-promise-executor-return': 'error',
    // https://github.com/qdanik/eslint-plugin-path
    'path/no-relative-imports': ['error', { maxDepth: 0 }],
    // https://eslint.org/docs/latest/rules/require-await
    'require-await': 'error',
    'no-restricted-properties': [
      'error',
      {
        object: 'crypto',
        property: 'randomUUID',
        message: 'Use of crypto.randomUUID is not allowed as it is not available in all browsers.',
      },
    ],
  },
  overrides: [
    // Disallow cross-imports between main and renderer processes
    {
      files: ['src/renderer/**/*'],
      rules: {
        'no-restricted-imports': [
          'error',
          {
            paths: [
              {
                name: '@/main',
                message:
                  'Files in @/renderer/* cannot import from @/main/*. If you need to access something from both, consider moving it to @/shared.',
              },
            ],
            patterns: ['@/main/**'],
          },
        ],
      },
    },
    {
      files: ['src/main/**/*'],
      rules: {
        'no-restricted-imports': [
          'error',
          {
            paths: [
              {
                name: '@/renderer',
                message:
                  'Files in @/main/* cannot import from @/renderer/*. If you need to access something from both, consider moving it to @/shared.',
              },
            ],
            patterns: ['@/renderer/**'],
          },
        ],
      },
    },
  ],
};
