import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, the Vite dev server (this process) only serves the SPA; API calls to
// /api/proxy/* are forwarded to the separate Express proxy process (server/index.js,
// started alongside this one by `npm run dev`), which is the one that attaches the
// backend API key. In production there is no Vite dev server at all — the same
// Express process serves the built static files AND the proxy (see server/index.js).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api/proxy': {
        target: process.env.VITE_PROXY_TARGET || 'http://localhost:3001',
        changeOrigin: true,
      },
    },
  },
})
