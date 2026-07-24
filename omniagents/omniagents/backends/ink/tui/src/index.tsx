/**
 * Entry point for omni-ink-tui
 */

import React from "react";
import { render } from "ink";
import meow from "meow";
import { App } from "./components/App.js";
import { KeypressProvider } from "./contexts/KeypressContext.js";
import { enableDebugLog, getDebugLogPath } from "./utils/debugLog.js";

const cli = meow(
  `
  Usage
    $ omni-ink-tui [options]

  Options
    --server, -s      WebSocket server URL (required)
    --name, -n        Agent name
    --token, -t       Authentication token
    --session         Session ID to resume
    --resume          Resume previous session
    --welcome, -w     Welcome message
    --initial-message Initial message to send
    --safe-mode       Enable safe mode (approval required)
    --debug           Enable debug logging
    --help            Show this help message
    --version         Show version

  Examples
    $ omni-ink-tui --server ws://localhost:8000/ws --name "MyAgent"
    $ omni-ink-tui -s ws://localhost:8000/ws -n "MyAgent" --session abc123
`,
  {
    importMeta: import.meta,
    flags: {
      server: {
        type: "string",
        shortFlag: "s",
        isRequired: true,
      },
      name: {
        type: "string",
        shortFlag: "n",
        default: "OmniAgent",
      },
      token: {
        type: "string",
        shortFlag: "t",
      },
      session: {
        type: "string",
      },
      resume: {
        type: "boolean",
        default: false,
      },
      welcome: {
        type: "string",
        shortFlag: "w",
      },
      initialMessage: {
        type: "string",
      },
      safeMode: {
        type: "boolean",
        default: false,
      },
      debug: {
        type: "boolean",
        default: false,
      },
      mouse: {
        type: "boolean",
        default: true,
      },
    },
  },
);

const {
  server,
  name,
  token,
  session,
  resume,
  welcome,
  initialMessage,
  debug,
  mouse,
} = cli.flags;

// Enable debug logging to file if requested
if (debug) {
  enableDebugLog();
  console.error(`Debug logging enabled: ${getDebugLogPath()}`);
}

// Disable terminal line wrapping - let Ink handle all wrapping
// This reduces rendering artifacts and flickering
process.stdout.write("\x1b[?7l");

// Mouse tracking is controlled by MouseProvider; no direct enable here

// Render the app
const app = render(
  <KeypressProvider>
    <App
      serverUrl={server}
      agentName={name}
      token={token}
      sessionId={session}
      resumeMode={resume}
      welcomeText={welcome}
      initialMessage={initialMessage}
      debug={debug}
      enableMouse={mouse}
    />
  </KeypressProvider>,
  {
    exitOnCtrlC: false,
    // Enable alternate buffer to eliminate flickering
    alternateBuffer: true,
    // Monitor slow renders in debug mode
    onRender: ({ renderTime }: { renderTime: number }) => {
      if (debug && renderTime > 200) {
        console.error(`[DEBUG] Slow render: ${renderTime}ms`);
      }
    },
  },
);

// Cleanup function to restore terminal state
const cleanup = () => {
  // Re-enable terminal line wrapping
  process.stdout.write("\x1b[?7h");
};

// Handle exit
app.waitUntilExit().then(() => {
  cleanup();
  process.exit(0);
});

// Handle interrupts
process.on("SIGINT", () => {
  cleanup();
  app.unmount();
  process.exit(0);
});

process.on("SIGTERM", () => {
  cleanup();
  app.unmount();
  process.exit(0);
});
