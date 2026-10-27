# Web and Ink Backends

`omniagents run --mode web` and `--mode ink` start the same runtime server
(`omniagents.backends.server.app.build_app`) and put a client in front of it:
a browser page for web, a terminal UI for Ink. Neither client speaks the
protocol by hand. Both use the monorepo's shared TypeScript packages:

| Package | What it is | Used by |
| --- | --- | --- |
| `@omniagents/client` (`packages/client`) | Protocol types, the RPC connection, conversation sessions, the transcript model | desktop, web UI, Ink TUI |
| `@omniagents/ui` (`packages/ui`) | Design system (shadcn components, themes) | desktop, web UI |
| `@omniagents/chat-ui` (`packages/chat-ui`) | The chat column, composer, approvals, terminal tabs | desktop, web UI |

So a conversation loads, sends, queues, recovers from a lost reply and
restores its pending questions the same way in every client.

## Web (`omniagents/backends/web`)

- `WebBackend.run` binds the server, serves the built SPA from
  `ui/dist/` (or `OMNI_WEB_UI_PATH`), and opens the browser on
  `http://host:port/?token=…`.
- `index.html` is served with `window.__OMNI_BOOT__` injected, carrying the
  server's `workspace_root`.
- The SPA (`ui/src/WebApp.tsx`) is the shared chat column plus the web's own
  pieces: a workspace picker (when the runtime offers `fs_list_dir`) and a
  terminal drawer (when it offers `terminal.create`). The open conversation's
  id lives in the `?session=` query, so a reload reopens it.
- The bearer token is exchanged for a one-time ticket before each socket
  dial; it never appears in a WebSocket URL.

## Ink (`omniagents/backends/ink`)

- `InkBackend.run` starts the server on a free localhost port and runs
  `node tui/dist/index.js --server ws://127.0.0.1:<port>/ws …` (or
  `OMNI_INK_TUI_PATH`). Node.js 22 or newer is required: the TUI uses Node's
  built-in WebSocket.
- The TUI (`tui/src`) holds the open conversation in a shared
  `ConversationSession` (`hooks/useConversation.ts`) and mirrors it into the
  store its Ink components render.

## Building and packaging

The UIs are npm workspaces; install once at the monorepo root.

```bash
npm ci
npm run build -w omniagents-web-ui   # → omniagents/backends/web/ui/dist
npm run build -w omni-ink-tui        # → omniagents/backends/ink/tui/dist
```

`make package-build` (in `omniagents/`) does both and builds the wheel.
Wheels carry only the two `dist/` directories. Nothing is built at run time:
a missing bundle is an error that says how to build it.

## Tests

- `tests/integration/test_ink_rpc.py` runs the TUI's conversation layer and
  real Ink screens against the real Python server across lost replies and
  reconnects (`tui/src/rpc/fixtures/python-server-client.ts`).
- The shared packages have their own suites (`npm test -w @omniagents/client`,
  and likewise for `ui` and `chat-ui`).
