import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const API = process.env.REDRUN_API ?? "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/health": API,
      "/samples": API,
      "/provider": API,
      "/run/stream": API,
      "/run": {
        target: API,
        // Client routes under /run belong to the SPA; the two API shapes proxy.
        bypass(req) {
          if (req.method === "POST") return null;
          if (req.url && req.url.includes("/events")) return null;
          return "/index.html";
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
