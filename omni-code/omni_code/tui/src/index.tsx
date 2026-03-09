import React, { useState, useCallback, useMemo, useEffect } from "react";
import { render } from "ink";
import meow from "meow";
import { App } from "./App.js";
import { initRpc, destroyRpc, api } from "./rpc.js";
import { ThemeContext } from "./ThemeContext.js";
import { themes, themeNames, type ThemeName } from "./theme.js";

const cli = meow(
  `
  Usage
    $ lazyomni [--rpc-read-fd 3 --rpc-write-fd 4] [--theme tokyo-night]
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
      theme: {
        type: "string",
        default: "tokyo-night",
      },
    },
  },
);

const initialThemeName = (cli.flags.theme ?? "tokyo-night") as ThemeName;

function Root() {
  const [themeName, setThemeNameRaw] = useState<ThemeName>(
    themes[initialThemeName] ? initialThemeName : "tokyo-night",
  );

  // Load persisted theme from env on mount
  useEffect(() => {
    api.getEnv().then((env) => {
      const saved = env.LAZYOMNI_THEME as string | undefined;
      if (saved && themeNames.includes(saved as ThemeName)) {
        setThemeNameRaw(saved as ThemeName);
      }
    }).catch(() => {});
  }, []);

  const setThemeName = useCallback((name: ThemeName) => {
    if (!themes[name]) return;
    setThemeNameRaw(name);
    // Persist to env (fire-and-forget)
    api.getEnv().then((env) => {
      api.saveEnv({ ...env, LAZYOMNI_THEME: name }).catch(() => {});
    }).catch(() => {});
  }, []);

  const ctxValue = useMemo(
    () => ({ theme: themes[themeName], themeName, setThemeName }),
    [themeName, setThemeName],
  );

  return (
    <ThemeContext.Provider value={ctxValue}>
      <App />
    </ThemeContext.Provider>
  );
}

// Disable line wrapping
process.stdout.write("\x1b[?7l");

const cleanup = () => {
  process.stdout.write("\x1b[?7h");
};

// Initialize RPC connection to Python server
initRpc(cli.flags.rpcReadFd, cli.flags.rpcWriteFd);

const app = render(<Root />, { exitOnCtrlC: false });

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
