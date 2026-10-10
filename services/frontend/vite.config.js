import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// macOS 风格设计系统将在后续以 CSS variables（design tokens）落地，见 platform-foundation spec。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/health": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});