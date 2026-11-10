import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: { port: 5191, proxy: { "/api": { target: process.env.VITE_API_PROXY ?? "http://localhost:8100", changeOrigin: false } } },
  build: {
    rollupOptions: {
      output: { manualChunks: { charts: ["recharts"], markdown: ["react-markdown", "remark-gfm"] } },
    },
  },
  test: { environment: "jsdom", globals: true, setupFiles: "./src/test/setup.ts", css: false },
});
