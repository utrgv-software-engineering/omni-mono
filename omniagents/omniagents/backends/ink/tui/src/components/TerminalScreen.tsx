import React, { useCallback, useEffect, useState } from "react";
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
  /** True while the PTY owns the keyboard. Lifted to App so the tab bar
   *  can show it; App also clears it whenever you leave the screen. */
  captured: boolean;
  onCapturedChange: (captured: boolean) => void;
};

// The Terminal screen (tab 5): a real shell, full height, in two states.
//
// Exactly one key cannot be shared — Tab, which shell completion needs
// and tab cycling needs. So the screen does not take the keyboard just
// because you arrived on it (that was the old behavior, and it made the
// tab cycle a one-way door: you could Tab in but never Tab out).
//
//   on arrival — the app owns the keyboard. Tab cycles tabs,
//     j/k/PgUp/PgDn scroll the scrollback, `i` or Enter hands the
//     keyboard to the shell.
//   typing — the PTY owns every key, Tab included. ctrl+] hands the
//     keyboard back; alt+1..5 still jump straight to a screen.
//
// ctrl+] means exactly one thing and nothing else: give the keyboard
// back. It is NOT a way to leave the screen — it used to double as a
// chat toggle, which made one key mean two things depending on a state
// the user could not see. Leaving is Tab and alt+1..5, the same as
// every other screen. The shell keeps running throughout; these states
// only decide who the keyboard belongs to.
/**
 * The footer hint.
 *
 * `ctrl+]` backs out exactly one level, so what it is labelled depends on
 * one thing: whether the terminal is captured. That label used to be
 * written into four separate branches and only two of them consulted
 * `captured` — the "no output yet" branch was tested FIRST, so a shell
 * that had not printed its prompt yet showed `● INPUT` beside
 * `ctrl+] chat` while the key actually released. Derived in one place
 * now, so the label cannot disagree with the handler.
 */
export function terminalHint({
  phase,
  captured,
  outputBytes,
  exitCode,
  error,
}: {
  phase: string;
  captured: boolean;
  outputBytes: number;
  exitCode: number | null;
  error?: string | null;
}): string {
  // ctrl+] only ever means "give the keyboard back", so it is only worth
  // mentioning while the terminal has it.
  const release = captured ? " · ctrl+] release" : "";
  if (phase === "exited") {
    const code = exitCode !== null ? ` ${exitCode}` : "";
    const why = error ? ` (${error})` : "";
    return `exited${code}${why} · enter restart`;
  }
  if (phase === "running" && outputBytes === 0 && !captured) {
    return "attached · no output yet — is the shell alive?";
  }
  return captured
    ? `keys → shell (tab too)${release} · alt+1..5 screens · PgUp/PgDn scroll`
    : "i or enter to type · tab cycles tabs · j/k PgUp/PgDn scroll";
}

export const TerminalScreen = ({
  active,
  height,
  client,
  sessionId,
  captured,
  onCapturedChange,
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
  useEffect(() => terminalSession.subscribe(() => setTick((n) => n + 1)), []);

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

  // Only a *captured* terminal owns the input surface: release mouse
  // tracking so the user's terminal stops generating reports (which
  // would forward into the shell as typed garbage). Until then the app
  // still owns the keyboard, so the wheel keeps scrolling normally.
  useEffect(() => {
    if (!active || !captured) return;
    mouse.suspend();
    return () => mouse.resume();
  }, [active, captured, mouse]);

  const phase = terminalSession.phase;

  // Publish the focus path to the status bar. The mode is the load-
  // bearing part: a mode you cannot see is a trap, and this one decides
  // whether Tab cycles tabs or completes a filename.
  useEffect(() => {
    if (!active) return;
    const suffix =
      phase === "exited"
        ? " · exited"
        : phase === "connecting"
          ? " · connecting"
          : phase === "error"
            ? " · error"
            : captured
              ? " · typing"
              : "";
    actions.setStatusContext(`term${suffix}`);
    return () => actions.setStatusContext(null);
  }, [active, phase, captured, actions]);

  const restart = useCallback(() => {
    if (!client || !sessionId) return;
    terminalSession.close();
    void terminalSession.open(client, sessionId, cols, rows);
  }, [client, sessionId, cols, rows]);

  useKeypress(
    (input, key) => {
      // Ctrl+] arrives as the raw GS byte (0x1d) - Ink only synthesizes
      // key.ctrl for the letter range.
      const backOut = (key.ctrl && input === "]") || input === "\u001d";

      if (!captured) {
        // The app owns the keyboard: only scrollback is ours, and
        // everything else falls through (returning false), which keeps
        // Tab cycling tabs and leaves ctrl+c, alt+digit and the rest
        // behaving as they do on every other screen.
        //
        // ctrl+] is swallowed rather than declined. It has one meaning —
        // give the keyboard back — and the keyboard is already given
        // back, so it does nothing here. Letting it through would make
        // one key mean "release" sometimes and "leave the screen" other
        // times, which is the ambiguity it was renamed out of.
        if (backOut) {
          return true;
        }
        if (phase === "exited" && key.return) {
          restart();
          return true;
        }
        if (input === "i" || key.return) {
          onCapturedChange(true);
          return true;
        }
        if (key.pageUp) {
          setScrollUp((prev) => prev + PAGE);
          return true;
        }
        if (key.pageDown) {
          setScrollUp((prev) => Math.max(0, prev - PAGE));
          return true;
        }
        if (key.upArrow || input === "k") {
          setScrollUp((prev) => prev + 1);
          return true;
        }
        if (key.downArrow || input === "j") {
          setScrollUp((prev) => Math.max(0, prev - 1));
          return true;
        }
        if (input === "G") {
          setScrollUp(0);
          return true;
        }
        return false;
      }

      // Captured: the PTY owns the keyboard, Tab included.
      if (backOut) {
        onCapturedChange(false);
        return true;
      }
      // Alt+1..5 stay reserved as direct jumps, handled by the App
      // accelerator below this handler. The range must match what App
      // actually binds: a key declined here that App does not bind is
      // swallowed outright - it never reaches the shell either. (This
      // declined 1..9 while App bound only 1..5, so alt+6..9 were dead.)
      if (key.meta && !key.escape && input >= "1" && input <= "5") {
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
        restart();
        return true;
      }
      const bytes = keyToBytes(input, key);
      if (bytes !== null) {
        setScrollUp(0);
        terminalSession.sendInput(bytes);
      }
      return true;
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
        {scrollUp > 0 && <Text color={Theme.stateBlocked}>↑{scrollUp} </Text>}
        {/* The mode marker leads the line: which of Tab's two meanings is
            live right now is the single most important thing here. */}
        {phase !== "exited" && phase !== "error" && (
          <Text
            color={captured ? Theme.stateActive : Theme.textChrome}
            bold={captured}
          >
            {captured ? "● typing " : "○ press i to type "}
          </Text>
        )}
        <Text color={Theme.textChrome}>
          {terminalHint({
            phase,
            captured,
            outputBytes: terminalSession.outputBytes,
            exitCode: terminalSession.exitCode,
            error: terminalSession.error,
          })}
        </Text>
      </Box>
    </Box>
  );
};
