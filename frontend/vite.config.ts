import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In development the FastAPI backend runs separately; proxy its routes so the app uses relative URLs.
const backend = process.env.LEASH_API ?? "http://localhost:8000";
const proxied = ["/api", "/v1", "/.well-known", "/paypal", "/fixture", "/webhooks", "/docs", "/openapi.json"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: Object.fromEntries(proxied.map((p) => [p, { target: backend, changeOrigin: false }])) },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 900 },
});
