/**
 * Entry point for omni-ink-tui
 */

import React from 'react';
import { render } from 'ink';
import meow from 'meow';
import { App } from './components/App.js';

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
        type: 'string',
        shortFlag: 's',
        isRequired: true,
      },
      name: {
        type: 'string',
        shortFlag: 'n',
        default: 'OmniAgent',
      },
      token: {
        type: 'string',
        shortFlag: 't',
      },
      session: {
        type: 'string',
      },
      resume: {
        type: 'boolean',
        default: false,
      },
      welcome: {
        type: 'string',
        shortFlag: 'w',
      },
      initialMessage: {
        type: 'string',
      },
      safeMode: {
        type: 'boolean',
        default: false,
      },
      debug: {
        type: 'boolean',
        default: false,
      },
    },
  }
);

const { server, name, token, session, welcome, initialMessage, debug } = cli.flags;

// Render the app
const app = render(
  <App
    serverUrl={server}
    agentName={name}
    token={token}
    sessionId={session}
    welcomeText={welcome}
    initialMessage={initialMessage}
    debug={debug}
  />
);

// Handle exit
app.waitUntilExit().then(() => {
  process.exit(0);
});

// Handle interrupts
process.on('SIGINT', () => {
  app.unmount();
  process.exit(0);
});

process.on('SIGTERM', () => {
  app.unmount();
  process.exit(0);
});
