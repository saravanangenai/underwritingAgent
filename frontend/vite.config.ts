import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    // The workbench calls the API at /api on its own origin, so the session cookie is
    // same-site. In development Vite forwards /api to the API; in production the web server
    // (Caddy) does the same.
    proxy: {
      '/api': {
        // 127.0.0.1, not localhost: Node may resolve localhost to ::1, where uvicorn is not listening.
        target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.spec.ts'],
  },
})
