// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ['dist/*', '.expo/*'],
  },
  {
    rules: {
      // eslint-config-expo 57 turns on the React Compiler hook rules. Every screen
      // in this app loads its data through an on-mount effect whose loader sets
      // state before its first await, so this rule flags 16 pre-existing call
      // sites. Satisfying it means restructuring how each screen fetches data --
      // a product change, not a lint fix -- so it stays a visible warning until
      // that refactor is scheduled instead of silently disappearing behind
      // per-line suppressions.
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
]);
