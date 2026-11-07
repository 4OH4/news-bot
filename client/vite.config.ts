import { readFileSync } from "node:fs";
import preact from "@preact/preset-vite";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";
import { NETWORK_ONLY_NAVIGATIONS } from "./src/sw-routes";

// The headers the API sets in production, so previews and Playwright run under them.
const securityHeaders: Record<string, string> = JSON.parse(
  readFileSync(new URL("./security-headers.json", import.meta.url), "utf8"),
);

export default defineConfig({
  define: { __APP_VERSION__: JSON.stringify(process.env.VITE_APP_VERSION ?? "dev") },
  preview: { headers: securityHeaders },
  plugins: [
    preact(),
    VitePWA({
      registerType: "autoUpdate",
      // Behind IAP a manifest fetched without the cookie gets a redirect, and Android then
      // makes a plain shortcut instead of an installable app.
      useCredentials: true,
      manifest: {
        name: "Newsfeed",
        short_name: "Newsfeed",
        start_url: "/",
        scope: "/",
        display: "standalone",
        theme_color: "#1a3a5c",
        background_color: "#1a3a5c",
        icons: [
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
          {
            src: "/icons/icon-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,png,webmanifest}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [...NETWORK_ONLY_NAVIGATIONS],
        cleanupOutdatedCaches: true,
      },
    }),
  ],
});
