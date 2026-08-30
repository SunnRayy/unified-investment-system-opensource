import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(() => {
  return {
    server: {
      port: 5003,
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: 'http://localhost:8008',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, '')
        },
        '/sync/stream': {
          target: 'http://localhost:8008',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, '')
        }
      }
    },
    build: {
      outDir: '../output/ux-command-center'
    },
    plugins: [react()],
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: './vitest.setup.ts'
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      }
    }
  };
});
