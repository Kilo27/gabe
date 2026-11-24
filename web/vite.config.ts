import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds into the Python package so `gabe web` serves it. `npm run dev` proxies the API.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../gabe/web/static", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
});
