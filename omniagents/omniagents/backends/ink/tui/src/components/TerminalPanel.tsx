import React, {
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import { Box, Text, useStdout } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "../rpc/client.js";
import { terminalSession } from "../terminal/session.js";
import { keyToBytes } from "../terminal/keys.js";
import { Theme } from "../theme.js";

const ROWS = 14;
const PAGE = 10;

type Props = {
  open: boolean;
  focused: boolean;
  client: RPCClient | null;
  sessionId: string | null;
};

// The docked terminal (ctrl+a → s): a real shell in the fixed
// bottom-panel region, running through the emulation seam. While
// focused, keystrokes forward to the PTY (ctrl+] detaches — the telnet
// convention; PgUp/PgDn stay ours for scrollback). The shell keeps
// running when the drawer closes; reopening reattaches to the same
// screen. Full-screen apps want the takeover escape hatch, not this.
export const TerminalPanel = React.memo<Props>(
  ({ open, focused, client, sessionId }) => {
    const { stdout } = useStdout();
    const width = stdout?.columns ?? 80;
    const cols = Math.max(20, width - 4);
    // Lines scrolled up from the live tail; 0 = following.
    const [scrollUp, setScrollUp] = useState(0);

    useSyncExternalStore(
      terminalSession.subscribe,
      terminalSession.getVersion,
      terminalSession.getVersion,
    );

    // Open lazily the first time the drawer shows; resize with the
    // terminal. The session survives the drawer closing.
    useEffect(() => {
      if (!open || !client || !sessionId) return;
      if (terminalSession.phase === "idle") {
        void terminalSession.open(client, sessionId, cols, ROWS);
      } else {
        terminalSession.resize(cols, ROWS);
      }
    }, [open, client, sessionId, cols]);

    useEffect(() => {
      if (!focused) setScrollUp(0);
    }, [focused]);

    useKeypress(
      (input, key) => {
        if (key.ctrl && input === "]") return false; // detach: App's chord
        if (key.tab && !key.ctrl) return false; // tab still cycles screens
        if (key.pageUp) {
          setScrollUp((prev) => prev + PAGE);
          return true;
        }
        if (key.pageDown) {
          setScrollUp((prev) => Math.max(0, prev - PAGE));
          return true;
        }
        if (terminalSession.phase === "exited" && key.return) {
          if (client && sessionId) {
            terminalSession.close();
            void terminalSession.open(client, sessionId, cols, ROWS);
          }
          return true;
        }
        const bytes = keyToBytes(input, key);
        if (bytes !== null) {
          setScrollUp(0);
          terminalSession.sendInput(bytes);
        }
        return true; // focused terminal swallows everything else
      },
      { isActive: focused, priority: KeypressPriority.Critical },
    );

    const lines = useMemo(() => {
      const state = terminalSession.state;
      if (!state) return [];
      const base = state.baseY();
      const start = Math.max(0, base - scrollUp);
      return state.readLines(start, ROWS);
    }, [
      scrollUp,
      // Version-driven: useSyncExternalStore re-renders on every batch.
      terminalSession.getVersion(),
    ]);

    if (!open) return null;

    const phase = terminalSession.phase;
    const title =
      phase === "running"
        ? "shell"
        : phase === "connecting"
          ? "shell · connecting"
          : phase === "exited"
            ? `shell · exited${terminalSession.exitCode !== null ? ` ${terminalSession.exitCode}` : ""}`
            : phase === "error"
              ? "shell · error"
              : "shell";

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={focused ? Theme.borderFocus : Theme.borderAmbient}
      >
        <Box>
          <Text color={Theme.textChrome} bold>
            {title}
          </Text>
          {phase === "connecting" && (
            <Text color={Theme.stateActive}>
              {" "}
              <Spinner type="dots" />
            </Text>
          )}
          {scrollUp > 0 && (
            <Text color={Theme.stateBlocked}> ↑{scrollUp}</Text>
          )}
        </Box>
        {phase === "error" ? (
          <Text color={Theme.stateFailed}>
            {terminalSession.error ?? "terminal failed"}
          </Text>
        ) : (
          lines.map((line, i) => (
            <Text key={i} wrap="truncate-end">
              {line.length > 0 ? line : " "}
            </Text>
          ))
        )}
        <Text color={Theme.textChrome}>
          {focused
            ? phase === "exited"
              ? "enter restart · ctrl+] detach"
              : "keys → shell · ctrl+] detach · PgUp/PgDn scroll"
            : "ctrl+a s focus"}
        </Text>
      </Box>
    );
  },
);
