import { build } from "esbuild";
import { chmodSync, mkdirSync, writeFileSync } from "fs";
import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

async function buildTUI() {
  try {
    const distDir = resolve(__dirname, "../dist");
    mkdirSync(distDir, { recursive: true });

    await build({
      entryPoints: [resolve(__dirname, "index.tsx")],
      bundle: true,
      platform: "node",
      target: "node18",
      outfile: resolve(distDir, "index.js"),
      format: "esm",
      sourcemap: true,
      minify: false,
      alias: {
        "react-devtools-core": resolve(__dirname, "shims/react-devtools-core.ts"),
      },
      banner: {
        js: `#!/usr/bin/env node
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const __filename = new URL(import.meta.url).pathname;
const __dirname = new URL('.', import.meta.url).pathname;
`,
      },
      define: {
        self: "globalThis",
      },
      external: ["node-pty"], // native addon, cannot be bundled
      mainFields: ["module", "main"],
      conditions: ["import", "module", "node"],
    });

    writeFileSync(resolve(distDir, "package.json"), JSON.stringify({ type: "module" }, null, 2));
    try {
      chmodSync(resolve(distDir, "index.js"), 0o755);
    } catch {}
  } catch (error) {
    console.error("Build failed:", error);
    process.exit(1);
  }
}

buildTUI();
