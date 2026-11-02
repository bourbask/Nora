import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    // Same-origin trick in dev: browser talks to Vite, /api is proxied to the
    // FastAPI proxy. No CORS, token never reaches the browser.
    proxy: {
      "/api": {
        target: "http://localhost:8068",
        headers: { Authorization: `Bearer ${process.env.NORA_API_TOKEN || "dev"}` },
      },
    },
  },
});
