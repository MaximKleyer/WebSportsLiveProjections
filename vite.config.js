import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { REPO_NAME } from './src/config.js';

// Single source of truth for the version shown in the footer.
const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url)));

// IMPORTANT: When deploying to GitHub Pages at <username>.github.io/<repo-name>/,
// `base` must match `/<repo-name>/`. Set REPO_NAME in src/config.js.
export default defineConfig({
  plugins: [react()],
  base: `/${REPO_NAME}/`,
  define: {
    // Replaced at build time so the footer can never drift from package.json.
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
});
