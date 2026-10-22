import { Console } from "node:console";
import type { RenderOptions } from "ink";

/**
 * Whether the TUI is drawing into a terminal multiplexer's pane rather
 * than straight onto a terminal. tmux and GNU screen export their own
 * variables to the programs they run, and their TERM names survive an
 * ssh hop from inside a pane where the variables do not.
 */
export function insideMultiplexer(env: NodeJS.ProcessEnv): boolean {
  if (env.TMUX || env.STY) return true;
  const term = env.TERM ?? "";
  return term.startsWith("tmux") || term.startsWith("screen");
}

const CONSOLE_METHODS = [
  "assert",
  "count",
  "countReset",
  "debug",
  "dir",
  "dirxml",
  "error",
  "group",
  "groupCollapsed",
  "groupEnd",
  "info",
  "log",
  "table",
  "time",
  "timeEnd",
  "timeLog",
  "trace",
  "warn",
] as const;

/**
 * Send every console method to `stderr` when `stderr` is not the
 * terminal, and say whether Ink still has to intercept the console.
 *
 * The launcher (InkBackend) points the TUI's stderr at the debug log or
 * /dev/null, so console output never reaches the screen. Ink's console
 * patch would still erase and repaint the whole alternate screen for
 * every line logged, which with --debug (a line per RPC message) is a
 * repaint per event. Only when stderr is the terminal itself does Ink
 * need to own the console to keep its frame intact.
 */
export function routeConsoleOffTerminal(
  stderr: NodeJS.WriteStream,
  target: Console = console,
): boolean {
  if (stderr.isTTY) return false;
  const offTerminal = new Console({ stdout: stderr, stderr });
  for (const method of CONSOLE_METHODS) {
    (target as unknown as Record<string, unknown>)[method] =
      offTerminal[method].bind(offTerminal);
  }
  return true;
}

/**
 * How the TUI asks Ink to paint the terminal.
 *
 * The app owns the whole screen (alternate buffer) and every frame is the
 * full screen height, so how a frame reaches the terminal decides whether
 * it flickers.
 *
 * - Ink's standard alternate-buffer writer homes the cursor, erases the
 *   screen and rewrites all of it on every frame. Incremental rendering
 *   diffs each frame against the previous one and rewrites only the lines
 *   that changed, in place, so nothing is ever erased between two frames.
 * - Synchronized output (DEC 2026) makes a terminal paint a frame
 *   atomically. tmux honours it, but repaints the entire pane for the
 *   outer terminal every time a synchronized update ends, so a one-line
 *   change (a spinner tick, a keystroke) becomes a full-screen rewrite
 *   that the outer terminal shows line by line. Under a multiplexer the
 *   in-place line diffs go out unsynchronized and tmux forwards only the
 *   cells that changed.
 */
export function inkRenderOptions({
  debug,
  env = process.env,
  consoleOffTerminal = false,
}: {
  debug: boolean;
  env?: NodeJS.ProcessEnv;
  /** routeConsoleOffTerminal() already moved the console off-screen. */
  consoleOffTerminal?: boolean;
}): RenderOptions {
  return {
    exitOnCtrlC: false,
    patchConsole: !consoleOffTerminal,
    alternateBuffer: true,
    incrementalRendering: true,
    synchronizedOutput: !insideMultiplexer(env),
    onRender: ({ renderTime }: { renderTime: number }) => {
      if (debug && renderTime > 200) {
        console.error(`[DEBUG] Slow render: ${renderTime}ms`);
      }
    },
  };
}
