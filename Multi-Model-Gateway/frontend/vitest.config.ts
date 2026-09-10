import { defineConfig } from 'vitest/config'
import { fileURLToPath, URL } from 'node:url'

// Separate vitest config (kept out of tsconfig.node.json / vite.config.ts) so
// the build-only vite.config stays typed against this project's own vite, and
// vitest's bundled vite never type-clashes with it.
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts'],
  },
})
