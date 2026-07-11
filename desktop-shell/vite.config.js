import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  base: './',
  plugins: [svelte()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/sse': {
        target: 'http://127.0.0.1:5758',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:5758',
        changeOrigin: true,
      },
    },
  },
});