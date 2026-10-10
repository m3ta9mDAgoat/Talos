import { defineConfig } from 'vite';

// Local development serves API calls from the same browser origin,
// so the Talos admin session cookie works without cross-site credentials.
export default defineConfig({
  server: {
    host: '0.0.0.0',
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
});
