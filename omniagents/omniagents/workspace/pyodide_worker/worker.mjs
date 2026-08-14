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

// --workspace=<host_dir>     If set, mount NODEFS at the mount point →
//                            host_dir. Files persist on host disk
//                            automatically.
// --mount-point=<vfs_path>   Where the workspace appears inside the
//                            interpreter (default /workspace). The wasm
//                            sandbox passes the HOST path itself so the
//                            shell (bashkit) and python agree on one
//                            uniform path namespace.
// --packages=<a,b,c>         Pre-load these pyodide-built packages.
// --snapshot=<path>          Read/write a serialized heap snapshot for fast
//                            warm starts (optional).
function parseArgs(args) {
  const out = {
    workspace: null,
    mountPoint: "/workspace",
    packages: [],
    snapshot: null,
    network: "none",
    networkAllow: [],
    networkBlocked: false,
  };
  for (const a of args) {
    if (a.startsWith("--workspace=")) out.workspace = a.slice("--workspace=".length);
    else if (a.startsWith("--mount-point=")) {
      const v = a.slice("--mount-point=".length).trim();
      if (v) out.mountPoint = v;
    } else if (a.startsWith("--packages=")) {
      const v = a.slice("--packages=".length).trim();
      if (v) out.packages = v.split(",").map((s) => s.trim()).filter(Boolean);
    } else if (a.startsWith("--snapshot=")) out.snapshot = a.slice("--snapshot=".length);
    // --network=none|open  and  --network-allow=host1,host2
    // Controls whether Python can egress. Pyodide exposes Node's real
    // fetch/XMLHttpRequest/WebSocket through its `js` module, so an
    // unrestricted worker has full network — which would silently defeat
    // a sandbox that advertises itself as offline (and auto-approves on
    // that basis). Default `none`: SAFE. The wasm sandbox passes the
    // profile's network policy explicitly.
    else if (a.startsWith("--network=")) out.network = a.slice("--network=".length).trim() || "none";
    else if (a.startsWith("--network-allow=")) {
      const v = a.slice("--network-allow=".length).trim();
      if (v) out.networkAllow = v.split(",").map((s) => s.trim()).filter(Boolean);
    }
    // --network-blocked: boot with the gate refusing (the wasm sandbox's
    // off-by-default) while keeping --network as the configured policy the
    // set_network toggle restores. Distinct from --network=none, which
    // deletes the egress globals outright and cannot be re-enabled.
    else if (a === "--network-blocked") out.networkBlocked = true;
  }
  return out;
}

const args = parseArgs(argv.slice(2));

// ---------------- stderr logger (stdout is reserved for JSON-RPC) -------- //

function log(...m) {
  stderr.write(`[pyodide-worker] ${m.join(" ")}\n`);
}

// The JS egress surfaces reachable from Python via the `js` module. Pyodide
// (Emscripten) raw sockets need a websocket proxy we never configure, so
// these are the ONLY paths out.
const NETWORK_GLOBALS = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource"];

// Mutable policy the gate wrappers consult on every call/construct. The
// `set_network` op (the sandbox.set_network live toggle) rewrites it; the
// wrappers themselves are installed once at boot, so references Python
// captured earlier still obey a later toggle. A --network-blocked boot
// starts the gate refusing ("none") while --network stays the configured
// policy the toggle restores.
const netPolicy = {
  mode: args.networkBlocked && args.network !== "none" ? "none" : (args.network || "none"),
  allow: new Set((args.networkAllow || []).map((h) => String(h).toLowerCase())),
};
let networkGateInstalled = false;
let pyodideHttpAttempted = false;

function hostPermitted(url) {
  let host;
  try {
    host = new URL(typeof url === "string" ? url : url.url).hostname.toLowerCase();
  } catch {
    return false;
  }
  // Exact host or dot-suffix match (allow "example.com" → "api.example.com").
  for (const entry of netPolicy.allow) {
    if (host === entry || host.endsWith("." + entry)) return true;
  }
  return false;
}

function applyNetworkPolicy() {
  if (args.network === "none") {
    // Explicitly offline (`network: none`): remove the globals outright —
    // the strongest form, and safe because such a profile reads as
    // toggle-unsupported (there is nothing to re-enable). `from js import
    // fetch` fails at import, matching the contained-and-offline contract.
    // A --network-blocked boot does NOT land here: it installs the gate
    // below with netPolicy.mode starting at "none".
    for (const name of NETWORK_GLOBALS) {
      try {
        delete globalThis[name];
      } catch {
        try {
          Object.defineProperty(globalThis, name, { value: undefined, configurable: true });
        } catch {
          /* best effort */
        }
      }
    }
    log("network: none (egress globals removed)");
    return;
  }
  // Booted with egress (open or allowlist): install policy-checking gates
  // so sandbox.set_network can flip enforcement live. fetch throws
  // synchronously when refused, so Python sees an immediate JsException
  // instead of an unhandled promise rejection.
  const realFetch = globalThis.fetch ? globalThis.fetch.bind(globalThis) : null;
  globalThis.fetch = (url, ...rest) => {
    if (netPolicy.mode === "open" && realFetch) return realFetch(url, ...rest);
    if (netPolicy.mode === "allow" && realFetch && hostPermitted(url)) {
      return realFetch(url, ...rest);
    }
    throw new Error(
      netPolicy.mode === "allow"
        ? `network: host not in sandbox allowlist: ${url}`
        : "network: sandbox is offline",
    );
  };
  // XHR/WebSocket/EventSource can't be host-filtered as cheaply; they work
  // only in open mode (fetch stays the sole egress path under an
  // allowlist), checked per call/construct so the toggle reaches captured
  // references too.
  for (const name of NETWORK_GLOBALS.filter((n) => n !== "fetch")) {
    const real = globalThis[name];
    if (typeof real !== "function") continue;
    const refuse = () => {
      throw new Error(
        netPolicy.mode === "allow"
          ? `network: ${name} unavailable in allowlist mode (fetch is the only egress path)`
          : "network: sandbox is offline",
      );
    };
    globalThis[name] = new Proxy(real, {
      construct(target, cargs) {
        if (netPolicy.mode !== "open") refuse();
        return Reflect.construct(target, cargs);
      },
      apply(target, thisArg, cargs) {
        if (netPolicy.mode !== "open") refuse();
        return Reflect.apply(target, thisArg, cargs);
      },
    });
  }
  networkGateInstalled = true;
  log(
    `network: gate installed (mode: ${netPolicy.mode}` +
      (netPolicy.mode === "allow" ? `, allow: ${[...netPolicy.allow].join(", ")})` : ")"),
  );
}

// Best-effort pyodide-http patch (urllib/requests support). Runs at boot for
// network-on sessions and lazily on the first set_network enable for
// blocked boots — it needs egress (micropip → PyPI) to install.
async function applyPyodideHttp(py) {
  if (pyodideHttpAttempted) return;
  pyodideHttpAttempted = true;
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
    py.FS.mkdirTree(args.mountPoint);
    py.FS.mount(py.FS.filesystems.NODEFS, { root: hostDir }, args.mountPoint);
    log(`mounted NODEFS ${args.mountPoint} → ${hostDir}`);
  } else {
    // MEMFS default — still create the dir so paths resolve.
    py.FS.mkdirTree(args.mountPoint);
    log(`using MEMFS ${args.mountPoint} (ephemeral)`);
  }

  // Apply pyodide-http patch so urllib/requests calls in user code don't
  // hang. Pointless (and would try to reach PyPI) when offline — skip it;
  // a blocked boot defers it to the first set_network enable.
  if (netPolicy.mode !== "none") {
    await applyPyodideHttp(py);
  }

  // Network policy. Pyodide's `js` module proxies the Node global scope, so
  // `from js import fetch` reaches Node's REAL fetch — full egress unless we
  // intervene. This is the single control point for Python network in the
  // wasm sandbox; the bashkit shell's curl/git builtins are gated
  // separately on the same profile policy.
  applyNetworkPolicy();

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

    // ----- network toggle (sandbox.set_network) ----- //

    set_network: async ({ network, network_allow = [] }) => {
      if (!networkGateInstalled) {
        // Booted with `network: none`: the globals were deleted, not
        // gated — there is nothing to re-enable.
        throw new Error("network toggle unsupported: worker booted offline");
      }
      const mode = network === "open" || network === "allow" ? network : "none";
      netPolicy.mode = mode;
      netPolicy.allow = new Set(
        (network_allow || []).map((h) => String(h).toLowerCase()),
      );
      // Blocked boots skipped the urllib/requests patch; catch up now
      // that egress exists (best effort, same as boot).
      if (mode !== "none") await applyPyodideHttp(py);
      log(`network: policy updated (mode: ${mode})`);
      return { ok: true, mode };
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
