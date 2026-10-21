import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: process.env.PAPER_DEMO_BUILD === "1" ? "/demo/paper-search/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", timeout: 600_000, proxyTimeout: 600_000 },
    },
  },
});
