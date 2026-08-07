import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // strictPort matters: apps/api/main.py allows ONLY http://localhost:5173.
    // Without it Vite silently falls back to 5174 when 5173 is busy and every
    // request dies with an opaque CORS error.
    port: 5173,
    strictPort: true,
    proxy: {
      '/chat': 'http://localhost:8000',
      '/approve': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/stream': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // SSE must not be buffered by the dev proxy or events arrive in a clump.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache, no-transform'
          })
        },
      },
    },
  },
})
