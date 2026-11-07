import { defineConfig } from "@playwright/test";

// The production build is served by `vite preview`, which applies security-headers.json,
// so every test runs under the CSP the API sets in production.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: "http://localhost:4173", serviceWorkers: "allow" },
  webServer: {
    command: "npm run build && npx vite preview --port 4173 --strictPort",
    url: "http://localhost:4173",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
