/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// The backend sets no CORS headers, so in development the UI calls the API on
// its own origin (`/v1/...`) and Vite forwards those calls to the FastAPI app.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          // Map and geometry libraries change rarely; keep them out of the app chunk.
          manualChunks: {
            map: ['leaflet', '@geoman-io/leaflet-geoman-free'],
            geo: ['@turf/area', '@turf/kinks'],
            react: ['react', 'react-dom'],
          },
        },
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/v1': {
          target: env.GEOSEARCH_API_PROXY_TARGET || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  };
});
