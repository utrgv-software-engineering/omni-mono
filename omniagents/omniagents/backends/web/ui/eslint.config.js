import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { globalIgnores } from 'eslint/config'

export default tseslint.config([
  // dist is build output; src/protocol/generated is emitted by the GUI
  // protocol codegen pipeline and must not be hand-edited to satisfy lint.
  globalIgnores(['dist', 'src/protocol/generated']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Empty `catch {}` is this UI's pervasive best-effort pattern for
      // cleanup that must never throw (WebSocket/audio/xterm teardown,
      // clipboard, localStorage). Every other empty block still errors.
      'no-empty': ['error', { allowEmptyCatch: true }],
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          // `_`-prefix marks intentionally-retained bindings (e.g. usage
          // state whose read side isn't built yet); rest-sibling
          // destructuring is the standard prop-stripping pattern.
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          ignoreRestSiblings: true,
        },
      ],
    },
  },
])
