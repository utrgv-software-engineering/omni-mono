import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useStdout } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "../rpc/client.js";
import { useActions } from "../store/appStore.js";
import { terminalSession } from "../terminal/session.js";
import { keyToBytes } from "../terminal/keys.js";
import { Theme } from "../theme.js";
import { useSyncExternalStore } from "react";

const PAGE = 10;

type Props = {
  active: boolean;
  height: number;
  client: RPCClient | null;
  sessionId: string | null;
  onExit: () => void;
};

// The Terminal screen (tab 5): a real shell, full height. While this
// screen is active the keyboard belongs to the PTY — including Tab
// (completion is non-negotiable), so this is the one screen the tab
// cycle enters but does not leave: ctrl+] toggles back to chat and
// alt+digit jumps stay reserved. The shell survives leaving the
// screen; returning reattaches to the same grid.
export const TerminalScreen = ({
  active,
  height,
  client,
  sessionId,
  onExit,
}: Props) => {
  const { stdout } = useStdout();
  const width = stdout?.columns ?? 80;
  const actions = useActions();
  const cols = Math.max(20, width);
  const rows = Math.max(4, height - 1); // hint line
  // Lines scrolled up from the live tail; 0 = following.
  const [scrollUp, setScrollUp] = useState(0);

  useSyncExternalStore(
    terminalSession.subscribe,
    terminalSession.getVersion,
    terminalSession.getVersion,
  );

  // Open lazily on first visit; track size while active. The session
  // outlives the screen.
  useEffect(() => {
    if (!active || !client || !sessionId) return;
    if (terminalSession.phase === "idle") {
      void terminalSession.open(client, sessionId, cols, rows);
    } else {
      terminalSession.resize(cols, rows);
    }
  }, [active, client, sessionId, cols, rows]);

  useEffect(() => {
    if (!active) setScrollUp(0);
  }, [active]);

  const phase = terminalSession.phase;

  // Publish the focus path to the status bar.
  useEffect(() => {
    if (!active) return;
    const suffix =
      phase === "exited"
        ? " · exited"
        : phase === "connecting"
          ? " · connecting"
          : phase === "error"
            ? " · error"
            : "";
    actions.setStatusContext(`term${suffix}`);
    return () => actions.setStatusContext(null);
  }, [active, phase, actions]);

  useKeypress(
    (input, key) => {
      // Reserved navigation: ctrl+] leaves, alt+digit jumps (handled by
      // the App accelerator below this handler).
      if (key.ctrl && input === "]") {
        onExit();
        return true;
      }
      if (key.meta && !key.escape && input >= "1" && input <= "9") {
        return false;
      }
      if (key.pageUp) {
        setScrollUp((prev) => prev + PAGE);
        return true;
      }
      if (key.pageDown) {
        setScrollUp((prev) => Math.max(0, prev - PAGE));
        return true;
      }
      if (phase === "exited" && key.return) {
        if (client && sessionId) {
          terminalSession.close();
          void terminalSession.open(client, sessionId, cols, rows);
        }
        return true;
      }
      const bytes = keyToBytes(input, key);
      if (bytes !== null) {
        setScrollUp(0);
        terminalSession.sendInput(bytes);
      }
      return true; // the terminal owns the keyboard, Tab included
    },
    { isActive: active, priority: KeypressPriority.Critical },
  );

  const lines = useMemo(() => {
    const state = terminalSession.state;
    if (!state) return [];
    const base = state.baseY();
    const start = Math.max(0, base - scrollUp);
    return state.readLines(start, rows);
  }, [
    scrollUp,
    rows,
    // Version-driven: useSyncExternalStore re-renders on every batch.
    terminalSession.getVersion(),
  ]);

  if (!active) return null;

  return (
    <Box flexDirection="column" height={height} width="100%" flexGrow={1}>
      {phase === "error" ? (
        <Box flexDirection="column" flexGrow={1}>
          <Text color={Theme.stateFailed}>
            {terminalSession.error ?? "terminal failed"}
          </Text>
        </Box>
      ) : (
        <Box flexDirection="column" flexGrow={1}>
          {lines.map((line, i) => (
            <Text key={i} wrap="truncate-end">
              {line.length > 0 ? line : " "}
            </Text>
          ))}
        </Box>
      )}

      <Box>
        {phase === "connecting" && (
          <Text color={Theme.stateActive}>
            <Spinner type="dots" />{" "}
          </Text>
        )}
        {scrollUp > 0 && (
          <Text color={Theme.stateBlocked}>↑{scrollUp} </Text>
        )}
        <Text color={Theme.textChrome}>
          {phase === "exited"
            ? `exited${terminalSession.exitCode !== null ? ` ${terminalSession.exitCode}` : ""}${terminalSession.error ? ` (${terminalSession.error})` : ""} · enter restart · ctrl+] chat`
            : phase === "running" && terminalSession.outputBytes === 0
              ? "attached · no output yet — is the shell alive? · ctrl+] chat"
              : "keys → shell (tab too) · ctrl+] chat · alt+1..4 screens · PgUp/PgDn scroll"}
        </Text>
      </Box>
    </Box>
  );
};
