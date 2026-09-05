import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(() => {
  return {
    server: {
      port: 5003,
      host: '0.0.0.0',
      proxy: {
        // Forward /api through UNCHANGED. Do not add a rewrite here.
        //
        // This used to strip the prefix, which worked only because the backend
        // mounts a second, unprefixed copy of every router as a local-dev
        // convenience — and it does that ONLY when neither UIS_GCS_BUCKET nor
        // UIS_AUTH_TOKEN is set (src/api/main.py, guarded by
        // tests/api/test_cloud_run_api_prefix.py). Setting UIS_AUTH_TOKEN, which
        // docs/quickstart.md tells a first-run user to do, removes that surface:
        // the browser asks for /api/auth/login, the proxy forwards /auth/login,
        // and the backend 404s. A new user following the documented steps in the
        // documented order hit a login dead end — the exact failure the doc was
        // written to prevent.
        //
        // The backend guard is not the thing to relax: BearerTokenMiddleware
        // exempts non-/api GETs so the SPA shell can load, so unprefixed routers
        // alongside a token would serve portfolio data unauthenticated. /api/* is
        // mounted in *both* modes, so forwarding the prefix intact is what makes
        // dev behave like production instead of diverging from it.
        '/api': {
          target: 'http://localhost:8008',
          changeOrigin: true
        }
        // Removed: a '/sync/stream' rule that was already inert. The frontend
        // only ever requests /api/sync/stream (matched by the rule above), and
        // its rewrite stripped a /api prefix that a path beginning /sync/stream
        // cannot have. It only suggested a second, unprefixed SSE surface that
        // does not exist.
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
