/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// The Tauri shell serves `dist/` over its asset protocol, so every asset URL is relative.
// `base: "./"` is what makes that work on all three platforms, and it is also why this must
// stay a plain static build: there is no server to resolve an absolute path.
export default defineConfig({
  base: "./",
  plugins: [svelte()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "safari15", // iOS 15 / Android WebView floor, and desktop is ahead of both
    sourcemap: false,
  },
  server: {
    port: 5179,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});