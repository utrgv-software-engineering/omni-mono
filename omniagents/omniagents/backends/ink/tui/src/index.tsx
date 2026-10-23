/**
 * Entry point for omni-ink-tui
 */

import React from "react";
import { render } from "ink";
import meow from "meow";
import { initTheme } from "./theme/controller.js";
import { App } from "./components/App.js";
import { KeypressProvider } from "./contexts/KeypressContext.js";
import { enableDebugLog, getDebugLogPath } from "./utils/debugLog.js";
import { inkRenderOptions, routeConsoleOffTerminal } from "./renderOptions.js";
import { CLIENT_IDENTITY } from "./client-identity.js";

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
    --theme           Color theme id (an Omarchy palette; default follows Omarchy, else tokyo-night)
    --themes-dir      Directory of user themes (<name>.toml or <name>/colors.toml)
    --settings-file   JSON file that remembers the chosen theme
    --help            Show this help message
    --version         Show version

  Examples
    $ omni-ink-tui --server ws://localhost:8000/ws --name "MyAgent"
    $ omni-ink-tui -s ws://localhost:8000/ws -n "MyAgent" --session abc123
`,
  {
    importMeta: import.meta,
    version: CLIENT_IDENTITY.version,
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
      theme: {
        type: "string",
      },
      themesDir: {
        type: "string",
      },
      settingsFile: {
        type: "string",
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
  theme,
  themesDir,
  settingsFile,
} = cli.flags;

// Before anything can log: console output belongs in stderr (the debug
// log or /dev/null under the launcher), never on the TUI's screen.
const consoleOffTerminal = routeConsoleOffTerminal(process.stderr);

// Enable debug logging to file if requested
if (debug) {
  enableDebugLog();
  console.error(`Debug logging enabled: ${getDebugLogPath()}`);
}

// Resolve the palette before the first frame: an explicit --theme, the
// saved choice, OMNI_THEME, Omarchy's current theme, else tokyo-night.
initTheme(theme, { themesDir, settingsFile });

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
  inkRenderOptions({ debug, consoleOffTerminal }),
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
