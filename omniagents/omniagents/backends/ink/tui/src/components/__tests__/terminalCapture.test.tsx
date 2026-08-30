/**
 * The terminal screen's keyboard-ownership contract.
 *
 * Exactly one key cannot be shared: Tab, which shell completion needs and
 * tab cycling needs. The screen resolves that with two states — browsing
 * (the app owns the keyboard, Tab cycles tabs) and captured (the PTY owns
 * it, Tab completes). The screen used to capture on arrival, which made
 * the tab cycle a one-way door: you could Tab in but never Tab out.
 *
 * What matters here is not what the screen *does* with a key but whether
 * it CONSUMES it. Consuming is what starves the app's Tab cycle. So these
 * tests register a spy at Low priority — standing in for App's real
 * handler, which sits at exactly that priority — and assert which keys
 * reach it. A key that reaches the spy is a key the app can still bind.
 */

import React from "react";
import { Writable } from "node:stream";
import { EventEmitter } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "ink";
import {
  KeypressProvider,
  KeypressPriority,
} from "../../contexts/KeypressContext.js";
import { MouseProvider } from "../../contexts/MouseContext.js";
import { useKeypress } from "../../hooks/useKeypress.js";
import { TerminalScreen } from "../TerminalScreen.js";
import { terminalSession } from "../../terminal/session.js";

const settle = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

class CaptureStream extends Writable {
  columns = 100;
  rows = 40;
  override _write(_chunk: Buffer, _enc: string, done: () => void): void {
    done();
  }
}

class FakeStdin extends EventEmitter {
  isTTY = true;
  private queue: string[] = [];
  setRawMode() {}
  setEncoding() {}
  resume() {}
  pause() {}
  read() {
    return this.queue.shift() ?? null;
  }
  unref() {}
  ref() {}
  /** Feed raw bytes the way a terminal would; Ink parses them into keys.
   *
   *  Ink 7 reads stdin in *paused* mode — `addListener("readable")` plus
   *  `read()` — so emitting "data" is silently ignored and every
   *  assertion about which handler ran passes vacuously. */
  press(sequence: string) {
    this.queue.push(sequence);
    this.emit("readable");
  }
}

/** Stands in for App's own handler, which is registered at Low priority.
 *
 *  Records the parsed key as well as the raw input: Ink reports Tab as
 *  `key.tab` with an EMPTY input string, so asserting on input alone
 *  would miss exactly the key this whole design is about. */
const FallThroughSpy = ({
  onKey,
}: {
  onKey: (input: string, key: { tab?: boolean; meta?: boolean }) => void;
}) => {
  useKeypress(
    (input, key) => {
      onKey(input, key);
    },
    { isActive: true, priority: KeypressPriority.Low },
  );
  return null;
};

type Harness = {
  stdin: FakeStdin;
  reached: ReturnType<typeof vi.fn>;
  onCapturedChange: ReturnType<typeof vi.fn>;
  onExit: ReturnType<typeof vi.fn>;
  unmount: () => void;
};

function mount(captured: boolean): Harness {
  const stdin = new FakeStdin();
  const reached = vi.fn();
  const onCapturedChange = vi.fn();
  const onExit = vi.fn();

  // No client: the session never opens, so sendInput is a documented
  // no-op (session.ts guards on phase). Dispatch is what is under test.
  const instance = render(
    <KeypressProvider>
      <MouseProvider>
        <FallThroughSpy onKey={reached} />
        <TerminalScreen
          active={true}
          height={20}
          client={null}
          sessionId={null}
          captured={captured}
          onCapturedChange={onCapturedChange}
          onExit={onExit}
        />
      </MouseProvider>
    </KeypressProvider>,
    {
      stdout: new CaptureStream() as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      debug: false,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );

  return {
    stdin,
    reached,
    onCapturedChange,
    onExit,
    unmount: () => instance.unmount(),
  };
}

const TAB = "\t";
const CTRL_RBRACKET = "\x1d"; // the GS byte Ctrl+] sends

describe("terminal screen keyboard ownership", () => {
  let harness: Harness | null = null;

  afterEach(() => {
    harness?.unmount();
    harness = null;
    terminalSession.close();
  });

  it("lets Tab through while browsing, so the tab cycle can leave", async () => {
    // The regression: arriving on the screen used to capture immediately
    // and swallow Tab, stranding anyone navigating by Tab alone.
    harness = mount(false);
    await settle();

    harness.stdin.press(TAB);
    await settle();

    expect(harness.reached).toHaveBeenCalled();
    const [, key] = harness.reached.mock.calls[0];
    expect(key.tab).toBe(true);
  });

  it("swallows Tab while captured, so the shell can complete", async () => {
    harness = mount(true);
    await settle();

    harness.stdin.press(TAB);
    await settle();

    expect(harness.reached).not.toHaveBeenCalled();
  });

  it("captures on i without disturbing the app", async () => {
    harness = mount(false);
    await settle();

    harness.stdin.press("i");
    await settle();

    expect(harness.onCapturedChange).toHaveBeenCalledWith(true);
    expect(harness.reached).not.toHaveBeenCalled();
  });

  it("gives the keyboard back on ctrl+], staying on the screen", async () => {
    harness = mount(true);
    await settle();

    harness.stdin.press(CTRL_RBRACKET);
    await settle();

    expect(harness.onCapturedChange).toHaveBeenCalledWith(false);
  });

  it("does nothing on ctrl+] once the keyboard is already back", async () => {
    // ctrl+] means one thing: release. With nothing to release it is
    // inert — it must not double as "leave the screen", and it must not
    // fall through to the app either, or one key would mean two things
    // depending on a mode the user cannot see.
    harness = mount(false);
    await settle();

    harness.stdin.press(CTRL_RBRACKET);
    await settle();

    expect(harness.onCapturedChange).not.toHaveBeenCalled();
    expect(harness.reached).not.toHaveBeenCalled();
    expect(harness.onExit).not.toHaveBeenCalled();
  });

  it("leaves on esc, like every other screen", async () => {
    // The terminal was the only screen Esc did nothing on: it declined
    // the key and App has no screen-exit branch, so it fell into the void
    // while every other screen advertised "esc chat".
    harness = mount(false);
    await settle();

    harness.stdin.press("\x1b");
    await settle();

    expect(harness.onExit).toHaveBeenCalled();
  });

  it("leaves esc to the shell while it owns the keyboard", async () => {
    // vim needs it.
    harness = mount(true);
    await settle();

    harness.stdin.press("\x1b");
    await settle();

    expect(harness.onExit).not.toHaveBeenCalled();
  });

  it("keeps alt+1..5 available as direct jumps while captured", async () => {
    harness = mount(true);
    await settle();

    harness.stdin.press("\x1b1");
    await settle();

    expect(harness.reached).toHaveBeenCalled();
  });

  it("does not strand alt+6..9 between the screen and the app", async () => {
    // These used to be declined here (the range said 1..9) while App bound
    // only 1..5 — so they reached neither the app nor the shell.
    harness = mount(true);
    await settle();

    harness.stdin.press("\x1b6");
    await settle();

    expect(harness.reached).not.toHaveBeenCalled();
  });
});
