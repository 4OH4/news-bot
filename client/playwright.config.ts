import { defineConfig } from "@playwright/test";

// The production build is served by `vite preview`, which applies security-headers.json,
// so every test runs under the CSP the API sets in production.
// Port is overridable (E2E_PORT) for running outside Docker. Under `make client-e2e` the server
// lives in the container's own network namespace, so the default never collides across worktrees.
const port = Number(process.env.E2E_PORT ?? 4173);

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: `http://localhost:${port}`, serviceWorkers: "allow" },
  webServer: {
    command: `npm run build && npx vite preview --port ${port} --strictPort`,
    url: `http://localhost:${port}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
