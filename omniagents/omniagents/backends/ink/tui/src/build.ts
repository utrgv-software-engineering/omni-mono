/**
 * Build script for omni-ink-tui
 * Uses esbuild to compile TypeScript and bundle dependencies
 */

import { build } from "esbuild";
import { chmodSync, mkdirSync, writeFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

async function buildTUI() {
  try {
    console.log("Building omni-ink-tui...");

    // Ensure dist directory exists
    const distDir = resolve(__dirname, "../dist");
    mkdirSync(distDir, { recursive: true });

    // Build with ESM format (required for top-level await in dependencies)
    await build({
      entryPoints: [resolve(__dirname, "index.tsx")],
      bundle: true,
      platform: "node",
      target: "node22",
      outfile: resolve(distDir, "index.js"),
      format: "esm",
      sourcemap: true,
      minify: false,
      external: [],
      banner: {
        js: `#!/usr/bin/env node
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const __filename = new URL(import.meta.url).pathname;
const __dirname = new URL('.', import.meta.url).pathname;
`,
      },
      // Define 'self' as globalThis for browser code that expects it
      define: {
        self: "globalThis",
      },
      // Prefer ESM versions of packages
      mainFields: ["module", "main"],
      conditions: ["import", "module", "node"],
    });

    // Create a package.json in dist to mark it as ESM
    const distPackageJson = {
      type: "module",
    };
    writeFileSync(
      resolve(distDir, "package.json"),
      JSON.stringify(distPackageJson, null, 2),
    );

    // Make the output executable
    const outfile = resolve(distDir, "index.js");
    try {
      chmodSync(outfile, 0o755);
    } catch (err) {
      // Ignore on Windows
    }

    console.log("✓ Build complete: dist/index.js");
    console.log("✓ Created dist/package.json with type: module");
  } catch (error) {
    console.error("Build failed:", error);
    process.exit(1);
  }
}

buildTUI();
