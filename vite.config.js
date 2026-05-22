import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { REPO_NAME } from './src/config.js';

// IMPORTANT: When deploying to GitHub Pages at <username>.github.io/<repo-name>/,
// `base` must match `/<repo-name>/`. Set REPO_NAME in src/config.js.
export default defineConfig({
  plugins: [react()],
  base: `/${REPO_NAME}/`,
});
