import { defineConfig } from "vite";
import { configDefaults } from "vitest/config";
import react from "@vitejs/plugin-react";

const devPort = Number(process.env.VITE_DEV_PORT) || 5173;

export default defineConfig({
  plugins: [react()],
  envPrefix: ["VITE_", "REACT_APP_"],
  server: { port: devPort, strictPort: true, host: true, allowedHosts: true, hmr: { clientPort: devPort, protocol: "ws", host: "localhost" } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.js",
    css: false,
    // UI tests type into many fields; under CPU load (e.g. right after the backend suite) 5 s is too tight.
    testTimeout: 20000,
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
