import React, { useLayoutEffect } from "react";
import { PassThrough } from "node:stream";
import { render } from "ink";
import { expect, it, vi } from "vitest";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { useKeypress, type Key } from "../useKeypress.js";

const terminal = vi.hoisted(() => ({
  dispatch: (_input: string, _key: Key) => {},
}));
vi.mock("../useTerminalInput.js", () => ({
  useTerminalInput: (handler: typeof terminal.dispatch, active = true) => {
    if (active) terminal.dispatch = handler;
  },
}));

it("installs the newly focused handler before layout effects can deliver input", async () => {
  const handled = vi.fn();
  const committed = vi.fn();
  function Prompt({ active }: { active: boolean }) {
    useKeypress(handled, { isActive: active });
    useLayoutEffect(() => {
      if (active) terminal.dispatch("y", {} as Key);
      committed(active);
    }, [active]);
    return null;
  }
  const stdout = new PassThrough();
  stdout.resume();
  const instance = render(
    <KeypressProvider>
      <Prompt active={false} />
    </KeypressProvider>,
    {
      stdout: stdout as unknown as NodeJS.WriteStream,
      stderr: stdout as unknown as NodeJS.WriteStream,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  try {
    await vi.waitFor(() => expect(committed).toHaveBeenCalledWith(false));
    instance.rerender(
      <KeypressProvider>
        <Prompt active />
      </KeypressProvider>,
    );
    await vi.waitFor(() => expect(committed).toHaveBeenCalledWith(true));
    expect(handled).toHaveBeenCalledWith("y", {});
  } finally {
    instance.unmount();
    instance.cleanup();
    stdout.destroy();
  }
});
