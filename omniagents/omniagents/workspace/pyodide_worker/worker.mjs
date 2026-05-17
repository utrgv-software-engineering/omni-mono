#!/usr/bin/env node
// Pyodide host worker.
//
// Loads pyodide once, then serves newline-delimited JSON-RPC over stdio:
//
//   request:  {"id": 1, "op": "exec_python", "code": "1+1"}
//   response: {"id": 1, "ok": true,  "result": {...}}
//             {"id": 1, "ok": false, "error": {"type": "...", "message": "..."}}
//
// Each request/response is one line. Binary payloads (file bytes) are base64
// strings, since stdio is text. The PyodideWorkspace on the Python side owns
// this subprocess and translates Workspace method calls into ops.
//
// Ops are deliberately small and orthogonal — file ops map straight to FS.*
// calls, code execution to runPythonAsync. Composite logic (glob, grep,
// recursive walk) is implemented Python-side via exec_python so we don't
// rebuild fnmatch/regex in JS.

import { loadPyodide } from "pyodide";
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { createInterface } from "node:readline";
import { stdin, stdout, stderr, argv, env } from "node:process";
import { Buffer } from "node:buffer";

// ---------------- argv / env --------------------------------------------- //

// --workspace=<host_dir>     If set, mount NODEFS at /workspace → host_dir.
//                            Files persist on host disk automatically.
// --packages=<a,b,c>         Pre-load these pyodide-built packages.
// --snapshot=<path>          Read/write a serialized heap snapshot for fast
//                            warm starts (optional).
function parseArgs(args) {
  const out = { workspace: null, packages: [], snapshot: null };
  for (const a of args) {
    if (a.startsWith("--workspace=")) out.workspace = a.slice("--workspace=".length);
    else if (a.startsWith("--packages=")) {
      const v = a.slice("--packages=".length).trim();
      if (v) out.packages = v.split(",").map((s) => s.trim()).filter(Boolean);
    } else if (a.startsWith("--snapshot=")) out.snapshot = a.slice("--snapshot=".length);
  }
  return out;
}

const args = parseArgs(argv.slice(2));

// ---------------- stderr logger (stdout is reserved for JSON-RPC) -------- //

function log(...m) {
  stderr.write(`[pyodide-worker] ${m.join(" ")}\n`);
}

// ---------------- pyodide bootstrap -------------------------------------- //

async function bootstrap() {
  const pyodideOpts = {};
  let snapshotBytes = null;
  if (args.snapshot && existsSync(args.snapshot)) {
    try {
      snapshotBytes = readFileSync(args.snapshot);
      pyodideOpts._loadSnapshot = snapshotBytes;
      log(`loading snapshot (${snapshotBytes.byteLength} bytes)`);
    } catch (e) {
      log(`snapshot load failed, cold-booting: ${e.message}`);
    }
  }

  const py = await loadPyodide(pyodideOpts);

  if (args.packages.length > 0) {
    log(`pre-loading packages: ${args.packages.join(", ")}`);
    await py.loadPackage(args.packages);
  }

  // Mount workspace. NODEFS = automatic round-trip to host disk.
  if (args.workspace) {
    const hostDir = resolve(args.workspace);
    if (!existsSync(hostDir)) mkdirSync(hostDir, { recursive: true });
    py.FS.mkdirTree("/workspace");
    py.FS.mount(py.FS.filesystems.NODEFS, { root: hostDir }, "/workspace");
    log(`mounted NODEFS /workspace → ${hostDir}`);
  } else {
    // MEMFS default — still create the dir so paths resolve.
    py.FS.mkdirTree("/workspace");
    log("using MEMFS /workspace (ephemeral)");
  }

  // Apply pyodide-http patch so urllib/requests calls in user code don't hang.
  // Pure-Python wheel; safe to no-op if absent.
  try {
    await py.loadPackage(["micropip"]);
    await py.runPythonAsync(`
import micropip
try:
    await micropip.install("pyodide-http")
    import pyodide_http
    pyodide_http.patch_all()
except Exception:
    pass
`);
  } catch (e) {
    log(`pyodide-http setup skipped: ${e.message}`);
  }

  // Heap snapshot (best effort; only supported in some pyodide builds).
  if (args.snapshot && snapshotBytes === null) {
    try {
      const bytes = py.makeMemorySnapshot();
      const { writeFileSync } = await import("node:fs");
      mkdirSync(dirname(args.snapshot), { recursive: true });
      writeFileSync(args.snapshot, Buffer.from(bytes));
      log(`wrote snapshot (${bytes.byteLength} bytes) → ${args.snapshot}`);
    } catch (e) {
      log(`snapshot save unsupported: ${e.message}`);
    }
  }

  return py;
}

// ---------------- op handlers -------------------------------------------- //

// Node's Buffer.from is pool-backed: the underlying ArrayBuffer can be much
// larger than the slice we care about, and the Buffer is just a view. Pyodide's
// NODEFS-side FS.writeFile path looks at .buffer/.byteOffset in a way that
// trips on pooled Buffers — we get a corrupted file with the pool's header
// bytes prepended. Copying into a freshly-allocated Uint8Array (whose
// underlying ArrayBuffer is exactly byteLength long) avoids the trap.
function b64ToFreshUint8(b64) {
  const buf = Buffer.from(b64, "base64");
  const out = new Uint8Array(buf.byteLength);
  out.set(buf);
  return out;
}

function makeHandlers(py) {
  const FS = py.FS;

  function statToObj(st) {
    return {
      size: st.size,
      mtime: st.mtime instanceof Date ? st.mtime.getTime() / 1000 : Number(st.mtime),
      mode: st.mode,
      // S_IFMT bits: 0o040000 = dir, 0o100000 = file, 0o120000 = symlink
      is_dir: (st.mode & 0o170000) === 0o040000,
      is_file: (st.mode & 0o170000) === 0o100000,
      is_symlink: (st.mode & 0o170000) === 0o120000,
    };
  }

  return {
    // Bootstrap check.
    hello: async () => ({ ok: true, pyodide_version: py.version }),

    // ----- file system ops (map straight to FS.*) ----- //

    fs_read: async ({ path }) => {
      const bytes = FS.readFile(path); // Uint8Array
      // Copy through an exactly-sized Uint8Array first to avoid the same
      // pooled-buffer offset trap that bites fs_write (see b64ToFreshUint8).
      const safe = new Uint8Array(bytes.byteLength);
      safe.set(bytes);
      return { bytes_b64: Buffer.from(safe).toString("base64") };
    },

    fs_write: async ({ path, bytes_b64, create_dirs = true }) => {
      if (create_dirs) {
        const parent = path.replace(/\/[^/]+$/, "");
        if (parent && parent !== "") {
          try { FS.mkdirTree(parent); } catch (_) { /* ignore */ }
        }
      }
      const data = b64ToFreshUint8(bytes_b64);
      FS.writeFile(path, data);
      return { written: data.byteLength };
    },

    fs_append: async ({ path, bytes_b64, create_dirs = true }) => {
      if (create_dirs) {
        const parent = path.replace(/\/[^/]+$/, "");
        if (parent && parent !== "") {
          try { FS.mkdirTree(parent); } catch (_) { /* ignore */ }
        }
      }
      const data = b64ToFreshUint8(bytes_b64);
      let existing;
      try {
        existing = FS.readFile(path);
      } catch (_) {
        existing = new Uint8Array(0);
      }
      const merged = new Uint8Array(existing.byteLength + data.byteLength);
      merged.set(existing, 0);
      merged.set(data, existing.byteLength);
      FS.writeFile(path, merged);
      return { written: data.byteLength, total: merged.byteLength };
    },

    fs_stat: async ({ path }) => {
      const st = FS.stat(path);
      return statToObj(st);
    },

    fs_lstat: async ({ path }) => {
      const st = FS.lstat(path);
      return statToObj(st);
    },

    fs_exists: async ({ path }) => {
      try {
        FS.stat(path);
        return { exists: true };
      } catch (_) {
        return { exists: false };
      }
    },

    fs_readdir: async ({ path }) => {
      // FS.readdir always includes "." and ".."; strip them.
      const raw = FS.readdir(path).filter((n) => n !== "." && n !== "..");
      const entries = raw.map((name) => {
        const child = path === "/" ? `/${name}` : `${path}/${name}`;
        try {
          const st = FS.lstat(child);
          return { name, ...statToObj(st) };
        } catch (_) {
          return { name, size: 0, mtime: 0, mode: 0, is_dir: false, is_file: false, is_symlink: false };
        }
      });
      return { entries };
    },

    fs_mkdir: async ({ path, parents = true }) => {
      if (parents) FS.mkdirTree(path);
      else FS.mkdir(path);
      return { ok: true };
    },

    fs_unlink: async ({ path }) => {
      FS.unlink(path);
      return { ok: true };
    },

    fs_rmdir: async ({ path }) => {
      FS.rmdir(path);
      return { ok: true };
    },

    // Recursive remove — convenience op so Python doesn't have to walk
    // the tree itself for every recursive=True call.
    fs_rmtree: async ({ path }) => {
      function rm(p) {
        let st;
        try { st = FS.lstat(p); } catch (_) { return; }
        if ((st.mode & 0o170000) === 0o040000) {
          for (const name of FS.readdir(p)) {
            if (name === "." || name === "..") continue;
            rm(p === "/" ? `/${name}` : `${p}/${name}`);
          }
          FS.rmdir(p);
        } else {
          FS.unlink(p);
        }
      }
      rm(path);
      return { ok: true };
    },

    fs_syncfs: async ({ populate = false } = {}) => {
      // For NODEFS this is a no-op (writes are immediate). For IDBFS /
      // NATIVEFS_ASYNC we'd push memory → disk here.
      await new Promise((resolve, reject) => {
        FS.syncfs(populate, (err) => (err ? reject(err) : resolve()));
      });
      return { ok: true };
    },

    // ----- python execution ----- //

    // exec_python: runs a code string. If `expression` is true we wrap with
    // `repr(eval(...))` so the caller gets a stringified result; otherwise
    // we just runPythonAsync the code and return whatever the last expr was.
    exec_python: async ({ code, capture_stdout = true, load_imports = false, timeout_s = null }) => {
      const stdoutBuf = [];
      const stderrBuf = [];

      if (capture_stdout) {
        py.setStdout({ batched: (s) => stdoutBuf.push(s) });
        py.setStderr({ batched: (s) => stderrBuf.push(s) });
      }

      try {
        if (load_imports) {
          try {
            await py.loadPackagesFromImports(code);
          } catch (e) {
            stderrBuf.push(`[loadPackagesFromImports] ${e.message}\n`);
          }
        }

        const runner = py.runPythonAsync(code);
        const value = timeout_s
          ? await Promise.race([
              runner,
              new Promise((_, reject) =>
                setTimeout(() => reject(new Error(`python exec timed out after ${timeout_s}s`)), timeout_s * 1000),
              ),
            ])
          : await runner;

        let resultRepr = null;
        if (value !== undefined && value !== null) {
          try {
            resultRepr = value.toString();
          } catch (_) {
            resultRepr = "<unrepresentable>";
          }
          if (value && typeof value.destroy === "function") {
            try { value.destroy(); } catch (_) {}
          }
        }
        return {
          stdout: stdoutBuf.join(""),
          stderr: stderrBuf.join(""),
          result_repr: resultRepr,
          exit_code: 0,
        };
      } catch (e) {
        return {
          stdout: stdoutBuf.join(""),
          stderr: stderrBuf.join("") + String(e?.message ?? e),
          result_repr: null,
          exit_code: 1,
        };
      } finally {
        if (capture_stdout) {
          py.setStdout({});
          py.setStderr({});
        }
      }
    },
  };
}

// ---------------- main loop ---------------------------------------------- //

async function main() {
  let py;
  try {
    py = await bootstrap();
  } catch (e) {
    stdout.write(JSON.stringify({ id: null, ok: false, error: { type: "bootstrap", message: e.message } }) + "\n");
    process.exit(1);
  }

  const handlers = makeHandlers(py);

  // Ready signal — Python side reads exactly one line, expects ok=true.
  stdout.write(JSON.stringify({ id: null, ok: true, ready: true, pyodide_version: py.version }) + "\n");

  const rl = createInterface({ input: stdin, crlfDelay: Infinity });

  // Serialize op execution: pyodide is single-threaded and concurrent
  // runPythonAsync calls would interleave globals in surprising ways.
  let pending = Promise.resolve();
  rl.on("line", (line) => {
    if (!line.trim()) return;
    pending = pending.then(async () => {
      let req;
      try { req = JSON.parse(line); } catch (e) {
        stdout.write(JSON.stringify({ id: null, ok: false, error: { type: "parse", message: e.message } }) + "\n");
        return;
      }
      const { id, op, params = {} } = req;
      const handler = handlers[op];
      if (!handler) {
        stdout.write(JSON.stringify({ id, ok: false, error: { type: "unknown_op", message: `unknown op: ${op}` } }) + "\n");
        return;
      }
      try {
        const result = await handler(params);
        stdout.write(JSON.stringify({ id, ok: true, result }) + "\n");
      } catch (e) {
        stdout.write(JSON.stringify({
          id,
          ok: false,
          error: { type: e?.name || "error", message: e?.message || String(e), errno: e?.errno ?? null },
        }) + "\n");
      }
    });
  });

  rl.on("close", () => {
    // stdin closed → parent gone → exit cleanly after draining pending.
    pending.finally(() => process.exit(0));
  });
}

main().catch((e) => {
  stderr.write(`[pyodide-worker] fatal: ${e?.stack || e?.message || e}\n`);
  process.exit(1);
});
