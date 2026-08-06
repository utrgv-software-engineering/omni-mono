import React, { useEffect, useState } from "react";
import { Box, Text, useStdout } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "../rpc/client.js";
import { useActions } from "../store/appStore.js";
import { terminalSession } from "../terminal/session.js";
import { keyToBytes } from "../terminal/keys.js";
import { Theme } from "../theme.js";
import { debugLog } from "../utils/debugLog.js";
import { useMouseContext } from "../contexts/MouseContext.js";

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
  const mouse = useMouseContext();
  const cols = Math.max(20, width);
  const rows = Math.max(4, height - 1); // hint line
  // Lines scrolled up from the live tail; 0 = following.
  const [scrollUp, setScrollUp] = useState(0);

  // Boring, explicit reactivity: subscribe → bump a counter. (The
  // useSyncExternalStore variant rendered fine in tests but a live
  // session sat frozen on its first paint — this primitive has no
  // snapshot-equality subtleties to be wrong about.)
  const [, setTick] = useState(0);
  useEffect(
    () => terminalSession.subscribe(() => setTick((n) => n + 1)),
    [],
  );

  // Open lazily on first visit; track size while active. The session
  // outlives the screen. A fresh TUI has no session until the first
  // message — ensure one server-side rather than silently doing
  // nothing (the web terminal's idiom).
  useEffect(() => {
    if (!active || !client) return;
    if (!sessionId) {
      void client
        .serverCall("session.ensure", {})
        .then((res) => {
          const sid = (res as { session_id?: string })?.session_id;
          if (typeof sid === "string" && sid) actions.setSessionId(sid);
        })
        .catch(() => {});
      return;
    }
    if (terminalSession.phase === "idle") {
      void terminalSession.open(client, sessionId, cols, rows);
    } else {
      terminalSession.resize(cols, rows);
    }
  }, [active, client, sessionId, cols, rows, actions]);

  useEffect(() => {
    if (!active) setScrollUp(0);
  }, [active]);

  // The terminal owns the input surface: release mouse tracking so the
  // user's terminal stops generating reports (which would forward into
  // the shell as typed garbage); restore on leave.
  useEffect(() => {
    if (!active) return;
    mouse.suspend();
    return () => mouse.resume();
  }, [active, mouse]);

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
      // the App accelerator below this handler). Ctrl+] arrives as the
      // raw GS byte (0x1d) — Ink only synthesizes key.ctrl for the
      // letter range.
      if ((key.ctrl && input === "]") || input === "\u001d") {
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

  // Recomputed every render — reading the grid is cheap at TUI frame
  // rates, and caching is exactly where a stale-screen bug can hide.
  const state = terminalSession.state;
  const lines = state
    ? state.readLines(Math.max(0, state.baseY() - scrollUp), rows)
    : [];
  useEffect(() => {
    if (!active) return;
    const firstContent = lines.find((l) => l.trim().length > 0);
    debugLog(
      `[term-ui] render v=${terminalSession.getVersion()} phase=${terminalSession.phase} rows=${rows} lines=${lines.length} first=${JSON.stringify((firstContent ?? "").slice(0, 40))}`,
    );
  });

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
