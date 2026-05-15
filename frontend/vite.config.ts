import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/health": "http://127.0.0.1:8000",
      "/companies": "http://127.0.0.1:8000",
      "/filings": "http://127.0.0.1:8000",
      "/jobs": "http://127.0.0.1:8000",
    },
  },
});
