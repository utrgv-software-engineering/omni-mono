import React from "react";
import stripAnsi from "strip-ansi";
import { describe, expect, it, vi, afterEach, beforeAll } from "vitest";
import {
  KeypressProvider,
  KeypressPriority,
} from "../../contexts/KeypressContext.js";
import { useKeypress } from "../../hooks/useKeypress.js";
import { SessionComposer, ComposerDrafts } from "../SessionComposer.js";
import {
  BaseSelectionList,
  SelectionInputContext,
} from "../shared/BaseSelectionList.js";
import { cleanup, render } from "ink-testing-library";

const inputHandlerStore = vi.hoisted(() => ({
  current: undefined as ((input: string, key: any) => void) | undefined,
}));

vi.mock("../../hooks/useTerminalInput.js", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../../hooks/useTerminalInput.js")>();
  return {
    ...actual,
    useTerminalInput: (handler: any, isActive = true) => {
      if (isActive) inputHandlerStore.current = handler;
      return actual.useTerminalInput(handler, isActive);
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

let MultiLineInputOptimized: React.ComponentType<any>;

beforeAll(async () => {
  ({ MultiLineInputOptimized } = await import("../MultiLineInputOptimized.js"));
});

const renderInput = (
  props: React.ComponentProps<typeof MultiLineInputOptimized>,
  wrap: (node: React.ReactElement) => React.ReactElement = (node) => node,
) => {
  if (!MultiLineInputOptimized) {
    throw new Error("MultiLineInputOptimized not loaded");
  }
  const instance = render(wrap(<MultiLineInputOptimized {...props} />));
  return { ...instance, instance };
};

afterEach(() => {
  cleanup();
  inputHandlerStore.current = undefined;
});

describe("MultiLineInputOptimized", () => {
  it("keeps ownership of an empty composer when its session id is assigned", async () => {
    const drafts = new ComposerDrafts();
    const onSubmit = vi.fn();
    const view = (sessionId?: string) => (
      <KeypressProvider>
        <SessionComposer
          drafts={drafts}
          sessionId={sessionId}
          onSubmit={onSubmit}
        />
      </KeypressProvider>
    );
    const { stdin, instance } = renderInput({}, () => view());
    await flush();
    const original = drafts.get();
    drafts.adoptSession("minted");
    instance.rerender(view("minted"));
    await flush();
    stdin.write("next draft");
    await flush();
    expect(drafts.get("minted")).toBe(original);
    expect(drafts.get("minted").current?.lines).toEqual(["next draft"]);
  });
  it("preserves separate drafts and cursors through session switches and screen unmounts", async () => {
    const drafts = new ComposerDrafts();
    const onSubmit = vi.fn();
    const view = (sessionId: string, shown = true) => (
      <KeypressProvider>
        {shown && (
          <SessionComposer
            drafts={drafts}
            sessionId={sessionId}
            onSubmit={onSubmit}
          />
        )}
      </KeypressProvider>
    );
    const { stdin, instance } = renderInput({}, () => view("a"));
    await flush();
    stdin.write("alpha\x1b[D");
    await flush();
    instance.rerender(view("b"));
    await flush();
    stdin.write("beta");
    await flush();
    instance.rerender(view("a", false));
    await flush();
    instance.rerender(view("a"));
    await flush();
    stdin.write("X\r");
    await flush();
    expect(onSubmit).toHaveBeenLastCalledWith("alphXa");
    instance.rerender(view("b"));
    await flush();
    stdin.write("\r");
    await flush();
    expect(onSubmit.mock.calls).toEqual([["alphXa"], ["beta"]]);
  });

  it("keeps question answers separate from the draft they interrupt", async () => {
    const drafts = new ComposerDrafts();
    const onSubmit = vi.fn();
    const view = (replyId?: string) => (
      <KeypressProvider>
        <SessionComposer
          drafts={drafts}
          sessionId="a"
          replyId={replyId}
          onSubmit={onSubmit}
        />
      </KeypressProvider>
    );
    const { stdin, instance } = renderInput({}, () => view());
    await flush();
    stdin.write("unfinished\x1b[D");
    await flush();
    instance.rerender(view("question:1"));
    await flush();
    stdin.write("\r");
    await flush();
    expect(onSubmit).not.toHaveBeenCalled();
    stdin.write("answer\r");
    await flush();
    instance.rerender(view());
    await flush();
    stdin.write("X\r");
    await flush();
    expect(onSubmit.mock.calls).toEqual([["answer"], ["unfinisheXd"]]);
  });

  it("allows offline editing and sends only after readiness returns", async () => {
    const drafts = new ComposerDrafts();
    const onSubmit = vi.fn();
    const view = (ready: boolean) => (
      <KeypressProvider>
        <SessionComposer
          drafts={drafts}
          sessionId="a"
          disableSubmit={!ready}
          onSubmit={onSubmit}
        />
      </KeypressProvider>
    );
    const { stdin, instance } = renderInput({}, () => view(false));
    await flush();
    stdin.write("offline\r");
    await flush();
    expect(onSubmit).not.toHaveBeenCalled();
    instance.rerender(view(true));
    await flush();
    stdin.write(" edits\r\r");
    await flush();
    expect(onSubmit.mock.calls).toEqual([["offline edits"]]);
  });

  it("restores a failed send after unmount without losing later typing or resending", async () => {
    const drafts = new ComposerDrafts();
    let fail!: (value: boolean) => void;
    const onSubmit = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          fail = resolve;
        }),
    );
    const view = (sessionId: string) => (
      <KeypressProvider>
        <SessionComposer
          drafts={drafts}
          sessionId={sessionId}
          onSubmit={onSubmit}
        />
      </KeypressProvider>
    );
    const { stdin, instance } = renderInput({}, () => view("a"));
    await flush();
    stdin.write("first\rnext\r\r");
    await flush();
    expect(onSubmit.mock.calls).toEqual([["first"]]);
    instance.rerender(view("b"));
    await flush();
    stdin.write("other");
    await flush();
    fail(false);
    await flush();
    expect(drafts.get("a").current?.lines).toEqual(["first", "next"]);
    expect(drafts.get("b").current?.lines).toEqual(["other"]);
    instance.rerender(view("a"));
    await flush();
    stdin.write("!\r");
    await flush();
    expect(onSubmit.mock.calls).toEqual([["first"], ["first\nnext!"]]);
    fail(true);
  });

  it("keeps the next draft when a new session receives its server id", async () => {
    const drafts = new ComposerDrafts();
    const onSubmit = vi.fn();
    const view = (sessionId?: string) => (
      <KeypressProvider>
        <SessionComposer
          drafts={drafts}
          sessionId={sessionId}
          onSubmit={onSubmit}
        />
      </KeypressProvider>
    );
    const { stdin, instance } = renderInput({}, () => view());
    await flush();
    stdin.write("first\rnext\x1b[D");
    await flush();
    drafts.adoptSession("minted");
    instance.rerender(view("minted"));
    await flush();
    stdin.write("X\r");
    await flush();
    expect(onSubmit.mock.calls).toEqual([["first"], ["nexXt"]]);
    expect(drafts.get().current).toBeUndefined();
  });

  it("does not let an unfocused decision or a paste consume a draft as an approval", async () => {
    const selected = vi.fn();
    const onSubmit = vi.fn();
    const drafts = new ComposerDrafts();
    const view = (focused: boolean) => (
      <KeypressProvider>
        <SelectionInputContext.Provider value={focused}>
          <BaseSelectionList
            options={[{ title: "Approve", description: "", value: "yes" }]}
            onSelect={selected}
          />
        </SelectionInputContext.Provider>
        <SessionComposer
          drafts={drafts}
          sessionId="a"
          onSubmit={onSubmit}
          disabled={focused}
        />
      </KeypressProvider>
    );
    const { stdin, instance } = renderInput({}, () => view(false));
    await flush();
    stdin.write("draft1");
    await flush();
    expect(selected).not.toHaveBeenCalled();
    instance.rerender(view(true));
    await flush();
    stdin.write("\x1b[200~1\x1b[201~");
    await flush();
    expect(selected).not.toHaveBeenCalled();
    stdin.write("1");
    await flush();
    expect(selected).toHaveBeenCalledWith("yes");
    instance.rerender(view(false));
    await flush();
    stdin.write("\r");
    await flush();
    expect(onSubmit).toHaveBeenCalledWith("draft1");
  });

  it("selects the navigated option when arrow and Enter arrive together", async () => {
    const selected = vi.fn();
    const { stdin } = renderInput({}, () => (
      <KeypressProvider>
        <BaseSelectionList
          options={[
            { title: "Approve", description: "", value: "yes" },
            { title: "Deny", description: "", value: "no" },
          ]}
          onSelect={selected}
        />
      </KeypressProvider>
    ));
    await flush();
    stdin.write("\x1b[B\r");
    await flush();
    expect(selected).toHaveBeenCalledWith("no");
  });

  it("restores the submitted cursor after a rejected RPC promise", async () => {
    const drafts = new ComposerDrafts();
    const onSubmit = vi
      .fn()
      .mockRejectedValueOnce(new Error("Disconnected"))
      .mockReturnValue(true);
    const { stdin } = renderInput({}, () => (
      <KeypressProvider>
        <SessionComposer drafts={drafts} sessionId="a" onSubmit={onSubmit} />
      </KeypressProvider>
    ));
    await flush();
    stdin.write("text\x1b[D\r");
    await flush();
    stdin.write("X\r");
    await flush();
    expect(onSubmit.mock.calls).toEqual([["text"], ["texXt"]]);
  });

  it("submits all edits received before React commits", async () => {
    const onSubmit = vi.fn();
    renderInput({ onSubmit });
    await flush();
    const handler = getInputHandler()!;
    handler("abc", baseKey);
    handler("X", baseKey);
    handler("", { ...baseKey, return: true });
    handler("next", baseKey);
    handler("", { ...baseKey, return: true });
    expect(onSubmit.mock.calls).toEqual([["abcX"], ["next"]]);
  });

  it("preserves edits when multiple keys arrive in a single stdin chunk", async () => {
    const onSubmit = vi.fn();
    const { stdin } = renderInput({ onSubmit });
    await flush();
    stdin.write("abc\x1b[DX\x7f\x7fY\r");
    await flush();
    expect(onSubmit).toHaveBeenCalledWith("aYc");
  });

  it("buffers fragmented arrows and distinguishes forward Delete", async () => {
    const onSubmit = vi.fn();
    const { stdin } = renderInput({ onSubmit });
    await flush();
    stdin.write("abc\x1b[");
    await flush();
    stdin.write("DX\x1b[3~\r");
    await flush();
    expect(onSubmit).toHaveBeenCalledWith("abX");
  });

  it("inserts bracketed multiline paste without submitting it", async () => {
    const onSubmit = vi.fn();
    const { stdin } = renderInput({ onSubmit });
    await flush();
    stdin.write("\x1b[20");
    await flush();
    stdin.write("0~first\r\nsecond\x1b[201");
    await flush();
    stdin.write("~");
    await flush();
    expect(onSubmit).not.toHaveBeenCalled();
    stdin.write("\r");
    await flush();
    expect(onSubmit).toHaveBeenCalledWith("first\nsecond");
  });

  it("bounds long wrapped input to displayed rows and follows the cursor", async () => {
    const onSubmit = vi.fn();
    const { stdin, lastFrame, stdout } = renderInput({
      onSubmit,
      maxHeight: 3,
    });
    await flush();
    stdin.write("a".repeat(1000) + "Z");
    await flush();
    expect(normalize(lastFrame())).toHaveLength(3);
    expect(stripAnsi(lastFrame()!)).toContain("Z");
    stdin.write("\x1b[H");
    await flush();
    expect(stripAnsi(lastFrame()!)).toContain("> ");
    // ink-testing-library's stdout is fixed at 100 columns; resize it as a
    // terminal does: new width, then a "resize" event.
    Object.defineProperty(stdout, "columns", { value: 45 });
    stdout.emit("resize");
    await flush();
    expect(normalize(lastFrame()).length).toBeLessThanOrEqual(3);
    stdin.write("X\r");
    await flush();
    expect(onSubmit).toHaveBeenCalledWith("X" + "a".repeat(1000) + "Z");
  });

  it("keeps a large paste intact", async () => {
    const onSubmit = vi.fn();
    const { stdin } = renderInput({ onSubmit, maxHeight: 3 });
    await flush();
    const text = "a".repeat(32000);
    stdin.write("\x1b[200~" + text + "\x1b[201~\r");
    await flush();
    expect(onSubmit).toHaveBeenCalledWith(text);
  });

  it("preserves global chords through the priority provider while consuming text", async () => {
    const global = vi.fn();
    const Global = () => {
      useKeypress(
        (text, key) => {
          global(text, key);
        },
        { isActive: true, priority: KeypressPriority.Low },
      );
      return null;
    };
    const onSubmit = vi.fn();
    const { stdin } = renderInput({ onSubmit }, (node) => (
      <KeypressProvider>
        <Global />
        {node}
      </KeypressProvider>
    ));
    await flush();
    stdin.write("abc\x1b[DX\r");
    await flush();
    expect(onSubmit).toHaveBeenCalledWith("abXc");
    expect(global).not.toHaveBeenCalled();
    stdin.write("\x02");
    await flush();
    expect(global).toHaveBeenCalledWith(
      "b",
      expect.objectContaining({ ctrl: true }),
    );
  });

  it("moves vertically across wrapped rows instead of jumping into history", async () => {
    const onSubmit = vi.fn();
    const { stdin } = renderInput({
      onSubmit,
      history: ["old history"],
      maxHeight: 3,
    });
    await flush();
    // ink-testing-library's 100 columns minus border/padding/gutter give 94
    // editable cells: 120 characters wrap with 26 on the second row.
    stdin.write("a".repeat(120) + "\x1b[AX\r");
    await flush();
    expect(onSubmit).toHaveBeenCalledWith(
      "a".repeat(26) + "X" + "a".repeat(94),
    );
  });

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
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(onEscape).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(normalize(lastFrame()).at(0)).toBe("> hi");
  });
});
