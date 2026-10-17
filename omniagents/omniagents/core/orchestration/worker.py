"""Private worker. Only JSON IPC crosses this boundary; no Python JS callbacks."""

import json
import sys
import time

try:
    import quickjs
except ImportError:
    from omniagents import _quickjs as quickjs  # type: ignore[no-redef]

FRAME_BYTES = 2_000_000
BOOTSTRAP = r"""
(() => {
  const stringify = JSON.stringify.bind(JSON), parse = JSON.parse.bind(JSON);
  const pending = new Map(); let next = 0, out = [], done = false, error = null;
  function request(method, params = {}) {
    if (next >= 256) return Promise.reject(new Error('Call limit exceeded'));
    const id = ++next;
    const promise = new Promise((resolve, reject) => pending.set(id, {resolve, reject}));
    out.push({id, method, params}); return promise;
  }
  globalThis.tools = Object.freeze({
    list: () => request('tools.list'),
    call: (name, args = {}) => request('tools.call', {name, args})
  });
  globalThis.session = Object.freeze({cells: Object.freeze({list: (options = {}) => request('cells.list', options)}), subagents: Object.freeze({list: (options = {}) => request('subagents.list', options), transcript: (options) => request('subagents.transcript', options)}), transcript: Object.freeze({list: (options = {}) => request('transcript.list', options)}), results: Object.freeze({
    list: (options = {}) => request('results.list', options),
    get: (reference) => request('results.get', {reference}),
    read: (options) => request('results.read', options)
  })});
  globalThis.image = (value) => out.push({method: 'media', value: typeof value === 'string' ? {type:'image', image_url:value} : {...value, type:'image'}});
  globalThis.file = (value) => out.push({method: 'media', value: {...value, type:'file'}});
  globalThis.text = (value) => {
    const text = typeof value === 'string' ? value : stringify(value);
    out.push({method: 'text', value: text ?? 'undefined'});
  };
  return {
    start: (source) => {
      const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
      try { new AsyncFunction(source)().then(() => {done = true}, e => {done = true; error = String(e)}) }
      catch(e) { done = true; error = String(e) }
    },
    deliver: (message) => {
      const m = parse(message), p = pending.get(m.id);
      if (!p) throw Error('Unknown response');
      pending.delete(m.id);
      if (m.error) p.reject(new Error(m.error)); else p.resolve(m.value);
    },
    drain: () => {const value = stringify({requests: out, done, error, pending: pending.size}); out = []; return value}
  };
})()
"""


def main():
    first = sys.stdin.buffer.readline(FRAME_BYTES + 1)
    if len(first) > FRAME_BYTES:
        raise ValueError("IPC frame exceeded")
    config = json.loads(first)
    if config.get("version") != 1:
        raise ValueError("Unsupported IPC version")
    ctx = quickjs.Context()
    ctx.set_memory_limit(64 * 1024 * 1024)
    ctx.set_max_stack_size(512 * 1024)
    cpu_start = time.process_time()

    def budget():
        remaining = config["cpu_seconds"] - (time.process_time() - cpu_start)
        if remaining <= 0:
            raise RuntimeError("JavaScript CPU budget exceeded")
        ctx.set_time_limit(remaining)

    budget()
    bridge = ctx.eval(BOOTSTRAP)
    # Object handles stay in Python, never in the JS global namespace.
    get = ctx.eval("(object, key) => object[key]")
    start, deliver, drain = (
        get(bridge, name) for name in ("start", "deliver", "drain")
    )
    start(config["code"])
    while True:
        budget()
        while ctx.execute_pending_job():
            budget()
        payload = drain()
        if len(payload.encode()) > FRAME_BYTES:
            raise RuntimeError("IPC output frame exceeded")
        state = json.loads(payload)
        print(payload, flush=True)
        if state["done"] and state["pending"] == 0:
            return
        if not state["pending"]:
            raise RuntimeError("JavaScript is waiting on an unresolved promise")
        line = sys.stdin.buffer.readline(FRAME_BYTES + 1)
        if not line:
            return
        if len(line) > FRAME_BYTES:
            raise ValueError("IPC frame exceeded")
        budget()
        deliver(line.decode())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"fatal": str(exc)[:1000]}), flush=True)
        sys.exit(1)
