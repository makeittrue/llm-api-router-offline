import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  base: "/admin/",
  build: {
    outDir: "../static/admin",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://localhost:8000",
      "/login": "http://localhost:8000",
      "/register": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/static": "http://localhost:8000",
    },
  },
});
