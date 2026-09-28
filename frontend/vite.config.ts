import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: API on :8081 (uvicorn investment_api.app:app --port 8081). Prod: Caddy serves dist/ and proxies /api.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8081" } },
});
