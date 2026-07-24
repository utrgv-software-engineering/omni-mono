import React from "react";
import stripAnsi from "strip-ansi";
import { describe, expect, it, vi, afterEach, beforeAll } from "vitest";
import { PassThrough } from "node:stream";

const inputHandlerStore = vi.hoisted(() => ({
  current: undefined as ((input: string, key: any) => void) | undefined,
}));

vi.mock("ink", async (importOriginal) => {
  const actual = await importOriginal<typeof import("ink")>();
  return {
    ...actual,
    useInput: (handler: any, options?: any) => {
      inputHandlerStore.current = handler;
      return actual.useInput(handler, options);
    },
  };
});

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const getInputHandler = () => inputHandlerStore.current;

const baseKey = {
  upArrow: false,
  downArrow: false,
  leftArrow: false,
  rightArrow: false,
  pageDown: false,
  pageUp: false,
  return: false,
  escape: false,
  ctrl: false,
  shift: false,
  tab: false,
  backspace: false,
  delete: false,
  meta: false,
};

const send = async (text: string, key: Partial<typeof baseKey> = {}) => {
  const handler = getInputHandler();
  expect(handler).toBeDefined();
  handler?.(text, { ...baseKey, ...key });
  await flush();
};

const normalize = (frame: string | undefined) => {
  if (!frame) return [];
  const lines = frame.split("\n").map((line) => stripAnsi(line));
  const content = /[┌┏╔]/u.test(lines[0]?.[0] ?? "")
    ? lines.slice(1, -1)
    : lines;
  return content
    .map((line) => line.replace(/^│\s?/u, "").replace(/\s?│$/u, ""))
    .map((line) => line.replace(/\s+$/u, ""));
};

class TestStdout extends PassThrough {
  frames: string[] = [];
  private last?: string;
  isTTY = true;
  columns = 120;
  rows = 40;

  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", (chunk) => {
      if (typeof chunk === "string") {
        this.frames.push(chunk);
        if (/[┌│>]/u.test(chunk)) {
          this.last = chunk;
        }
      }
    });
  }

  lastFrame() {
    if (this.last) {
      return this.last;
    }
    for (let index = this.frames.length - 1; index >= 0; index -= 1) {
      const frame = this.frames[index];
      if (/[┌│>]/u.test(frame)) {
        return frame;
      }
    }
    return undefined;
  }

  ref() {}

  unref() {}
}

class TestStderr extends PassThrough {
  frames: string[] = [];
  private last?: string;
  isTTY = true;

  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", (chunk) => {
      if (typeof chunk === "string") {
        this.frames.push(chunk);
        this.last = chunk;
      }
    });
  }

  lastFrame() {
    return this.last;
  }

  ref() {}

  unref() {}
}

class TestStdin extends PassThrough {
  isTTY = true;

  constructor() {
    super();
    this.setEncoding("utf8");
  }

  setRawMode(_mode: boolean) {}

  resume() {
    super.resume();
    return this;
  }

  pause() {
    super.pause();
    return this;
  }

  ref() {}

  unref() {}
}

type InkModule = typeof import("ink");
type InkRender = InkModule["render"];

const instances: Array<ReturnType<InkRender>> = [];

let MultiLineInputOptimized: React.ComponentType<any>;
let inkRender: InkRender;

beforeAll(async () => {
  const inkModule = await import("ink");
  inkRender = inkModule.render;
  ({ MultiLineInputOptimized } = await import("../MultiLineInputOptimized.js"));
});

const renderInput = (
  props: React.ComponentProps<typeof MultiLineInputOptimized>,
) => {
  if (!MultiLineInputOptimized) {
    throw new Error("MultiLineInputOptimized not loaded");
  }
  if (!inkRender) {
    throw new Error("Ink render not loaded");
  }
  const stdout = new TestStdout();
  const stderr = new TestStderr();
  const stdin = new TestStdin();
  const instance = inkRender(<MultiLineInputOptimized {...props} />, {
    stdout: stdout as unknown as NodeJS.WriteStream,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    debug: true,
    exitOnCtrlC: false,
    patchConsole: false,
  });
  instances.push(instance);
  return {
    stdin,
    lastFrame: () => stdout.lastFrame(),
  };
};

afterEach(() => {
  while (instances.length) {
    const instance = instances.pop();
    instance?.unmount();
    instance?.cleanup();
  }
  inputHandlerStore.current = undefined;
});

describe("MultiLineInputOptimized", () => {
  it("renders placeholder with visible cursor", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({
      onSubmit,
      placeholder: "Say something",
    });
    await flush();
    const frame = lastFrame();
    expect(frame).toBeDefined();
    expect(frame).toContain("\u001b[7m");
    expect(normalize(frame).at(0)).toBe(">");
  });

  it("updates content as characters are typed", async () => {
    const onSubmit = vi.fn();
    const { stdin, lastFrame } = renderInput({ onSubmit });
    stdin.write("a");
    await flush();
    stdin.write("b");
    await flush();
    stdin.write("c");
    await flush();
    const frame = lastFrame();
    expect(normalize(frame).at(0)).toBe("> abc");
    expect(frame).toContain("\u001b[7m");
  });

  it("treats raw DEL (0x7f) as backspace even without key flags", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();

    await send("abc");
    await send("\x7f");

    expect(normalize(lastFrame()).at(0)).toBe("> ab");
  });

  it("strips ANSI escape codes during paste and keeps visible text", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();

    await send("\u001b[200~\u001b[31mred\u001b[0m\nline2\u001b[201~");

    expect(normalize(lastFrame())).toEqual(["> red", "  line2"]);
  });

  it("submits trimmed content on enter", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();
    // Type " test "
    await send(" test ");
    // Press enter to submit
    await send("", { return: true });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("test");
    const frame = lastFrame();
    expect(normalize(frame).at(0)).toBe(">");
  });

  it("navigates history entries", async () => {
    const onSubmit = vi.fn();
    const history = ["first entry", "second entry"];
    const { stdin, lastFrame } = renderInput({ onSubmit, history });
    await flush();
    stdin.write("\u001b[A");
    await flush();
    expect(normalize(lastFrame()).at(0)).toBe("> second entry");
    stdin.write("\u001b[A");
    await flush();
    expect(normalize(lastFrame()).at(0)).toBe("> first entry");
    stdin.write("\u001b[B");
    await flush();
    expect(normalize(lastFrame()).at(0)).toBe("> second entry");
  });

  it("merges lines when backspacing at new line start", async () => {
    const onSubmit = vi.fn();
    const history = ["first line\nsecond line"];
    const { stdin, lastFrame } = renderInput({ onSubmit, history });
    await flush();
    stdin.write("\u001b[A");
    await flush();
    stdin.write("\u001b[B");
    await flush();
    stdin.write("\u007f");
    await flush();
    expect(normalize(lastFrame())).toEqual(["> first linesecond line"]);
  });

  it("ignores input events while disabled", async () => {
    const onSubmit = vi.fn();
    const { stdin, lastFrame } = renderInput({
      onSubmit,
      disabled: true,
      placeholder: "disabled",
    });
    await flush();
    stdin.write("abc");
    await flush();
    expect(normalize(lastFrame()).at(0)).toBe(">");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("handles multi-line paste by splitting into separate rows", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();
    // Simulate pasting multi-line text by calling handler directly with the text
    await send("foo\nbar");
    expect(normalize(lastFrame())).toEqual(["> foo", "  bar"]);
  });

  it("preserves pasted CR-only line endings", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();
    await send("foo\rbar");
    expect(normalize(lastFrame())).toEqual(["> foo", "  bar"]);
  });

  it("restores draft after navigating history", async () => {
    const onSubmit = vi.fn();
    const history = ["older message"];
    const { stdin, lastFrame } = renderInput({ onSubmit, history });
    await flush();
    stdin.write("draft text");
    await flush();
    stdin.write("\u001b[A");
    await flush();
    expect(normalize(lastFrame()).at(0)).toBe("> older message");
    stdin.write("\u001b[B");
    await flush();
    expect(normalize(lastFrame()).at(0)).toBe("> draft text");
  });

  it("inserts newline on ctrl+enter without submitting", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();
    await send("", { return: true, ctrl: true });
    expect(onSubmit).not.toHaveBeenCalled();
    const normalized = normalize(lastFrame());
    expect(normalized.at(0)).toBe("> Type your message...");
    expect(normalized.at(1)).toBe("");
  });

  it("does not submit when disableSubmit is true", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit, disableSubmit: true });
    await flush();
    // Type "hello"
    await send("hello");
    // Press enter (should not submit because disableSubmit is true)
    await send("", { return: true });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(normalize(lastFrame()).at(0)).toBe("> hello");
  });

  it("uses forward delete for ctrl+d", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();

    await send("abc");
    await send("", { leftArrow: true });
    await send("", { leftArrow: true });
    await send("d", { ctrl: true });

    expect(normalize(lastFrame()).at(0)).toBe("> ac");
  });

  it("does not show placeholder when there is content on later lines", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();

    await send("", { return: true, ctrl: true });
    await send("bar");

    expect(normalize(lastFrame())).toEqual([">", "  bar"]);
  });

  it("ignores SGR mouse escape sequences", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();

    await send("\x1b[<64;10;20M");

    expect(normalize(lastFrame()).at(0)).toBe(">");
  });

  it("ignores SGR mouse escape sequences without escape prefix", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();

    await send("[<0;31;13M");

    expect(normalize(lastFrame()).at(0)).toBe(">");
  });

  it("handles emoji cursor movement and deletion", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit });
    await flush();

    await send("😀x");
    await send("", { leftArrow: true });
    await send("", { backspace: true });

    expect(normalize(lastFrame()).at(0)).toBe("> x");
  });

  it("calls onEscape without clearing content", async () => {
    const onEscape = vi.fn();
    const onSubmit = vi.fn();
    const { stdin, lastFrame } = renderInput({ onSubmit, onEscape });
    stdin.write("hi");
    await flush();
    stdin.write("\u001b");
    await flush();
    expect(onEscape).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(normalize(lastFrame()).at(0)).toBe("> hi");
  });
});
