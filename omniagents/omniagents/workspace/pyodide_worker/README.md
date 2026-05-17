# pyodide_worker

Node host for `omniagents.workspace.pyodide.PyodideWorkspace`. Loaded as a
long-lived subprocess; serves newline-delimited JSON-RPC over stdio.

## Install

```sh
cd omniagents/workspace/pyodide_worker
npm install
```

`pyodide` is the only runtime dep. The Python side discovers `worker.mjs` via
package data — installs to the wheel automatically.

## Run standalone (debugging)

```sh
node worker.mjs --workspace=/tmp/ws --packages=numpy,pandas
```

On startup the worker writes one `{"ready": true, ...}` line, then waits for
JSON requests on stdin.

## Op surface

See the top of `worker.mjs` for the full list. Two families:

- **`fs_*`** — direct `FS.*` calls (read, write, stat, readdir, mkdir,
  unlink, rmdir, rmtree, syncfs). Bytes are base64.
- **`exec_python`** — `runPythonAsync(code)` with stdout/stderr capture and
  optional `loadPackagesFromImports`.

The Python side composes recursive `glob`/`grep`/`walk` on top of `fs_*` via
`exec_python` calls — we don't rebuild fnmatch/regex in JS.
