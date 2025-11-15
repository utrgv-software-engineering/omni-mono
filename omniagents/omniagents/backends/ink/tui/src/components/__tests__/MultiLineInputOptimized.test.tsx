import React from "react";
import stripAnsi from "strip-ansi";
import { describe, expect, it, vi, afterEach } from "vitest";
import { render as inkRender } from "ink";
import { PassThrough } from "node:stream";
import { MultiLineInputOptimized } from "../MultiLineInputOptimized.js";

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const normalize = (frame: string | undefined) => {
  if (!frame) return [];
  const lines = frame.split("\n").map((line) => stripAnsi(line));
  const content = /[┌┏╔]/u.test(lines[0]?.[0] ?? "") ? lines.slice(1, -1) : lines;
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

  write(frame: string, encoding?: BufferEncoding, cb?: (error?: Error | null) => void) {
    return super.write(frame, encoding, cb);
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

  write(frame: string, encoding?: BufferEncoding, cb?: (error?: Error | null) => void) {
    return super.write(frame, encoding, cb);
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

  write(data: string, encoding?: BufferEncoding, cb?: (error?: Error | null) => void) {
    return super.write(data, encoding, cb);
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

const instances: Array<ReturnType<typeof inkRender>> = [];

const renderInput = (props: React.ComponentProps<typeof MultiLineInputOptimized>) => {
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
});

describe("MultiLineInputOptimized", () => {
  it("renders placeholder with visible cursor", async () => {
    const onSubmit = vi.fn();
    const { lastFrame } = renderInput({ onSubmit, placeholder: "Say something" });
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

  it("submits trimmed content on enter", async () => {
    const onSubmit = vi.fn();
    const { stdin, lastFrame } = renderInput({ onSubmit });
    stdin.write(" test ");
    await flush();
    stdin.write("\r");
    await flush();
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
    const { stdin, lastFrame } = renderInput({ onSubmit });
    stdin.write("foo\nbar");
    await flush();
    expect(normalize(lastFrame())).toEqual(["> foo", "  bar"]);
  });
});
