import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    restoreMocks: true,
    clearMocks: true,
    env: {
      FORCE_COLOR: "1"
    },
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"]
    }
  }
});
