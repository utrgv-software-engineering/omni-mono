import React from "react";
import { render } from "ink";
import meow from "meow";
import { App } from "./App.js";
import { initRpc, destroyRpc } from "./rpc.js";

const cli = meow(
  `
  Usage
    $ lazyomni [--rpc-read-fd 3 --rpc-write-fd 4]
`,
  {
    importMeta: import.meta,
    flags: {
      app: {
        type: "string",
        default: "lazyomni",
      },
      rpcReadFd: {
        type: "number",
        default: 3,
      },
      rpcWriteFd: {
        type: "number",
        default: 4,
      },
      debug: {
        type: "boolean",
        default: false,
      },
    },
  },
);

// Disable line wrapping
process.stdout.write("\x1b[?7l");

const cleanup = () => {
  process.stdout.write("\x1b[?7h");
};

// Initialize RPC connection to Python server
initRpc(cli.flags.rpcReadFd, cli.flags.rpcWriteFd);

const app = render(<App />, {
  exitOnCtrlC: false,
});

app.waitUntilExit().then(() => {
  cleanup();
  destroyRpc();
});

process.on("SIGINT", () => {
  cleanup();
  destroyRpc();
  app.unmount();
});

process.on("SIGTERM", () => {
  cleanup();
  destroyRpc();
  app.unmount();
});
