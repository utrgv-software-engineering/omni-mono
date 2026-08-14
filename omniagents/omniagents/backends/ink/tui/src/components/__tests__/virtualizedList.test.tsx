/**
 * Viewport regression tests for VirtualizedList, and for the loop it forms
 * with its parent Scrollable.
 *
 * The shipped bug these guard: a long assistant message blanked the whole
 * transcript, and scrolling showed content for a single frame before it
 * vanished again. Two defects combined into an oscillator —
 *
 *   1. VirtualizedList's offsets memo listed only [data, keyExtractor,
 *      estimatedItemHeight] but read a mutated-in-place `heights` Map, so
 *      measurements taken after the last `data` change never reached the
 *      offset table. This list's coordinate space (estimated rows) drifted
 *      away from the parent Scrollable's (measured rows).
 *   2. With scrollTop past the end of that stale table the binary search
 *      returned first > last, `data.slice` came back empty, and the viewport
 *      held nothing but a spacer. Scrollable never clamped the now
 *      out-of-range scrollTop, so the blank was sticky: scrolling clamped
 *      against the collapsed geometry, content rendered, growth re-armed
 *      pin-to-bottom, and the viewport blanked again.
 *
 * The two layers own different halves and are tested apart: VirtualizedList
 * owns "which rows are in the slice", Scrollable owns "scrollTop stays in
 * range". Only the integration test at the bottom can see the oscillation,
 * because it needs both.
 */

import React from "react";
import stripAnsi from "strip-ansi";
import { PassThrough } from "node:stream";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

class TestStdout extends PassThrough {
  frames: string[] = [];
  isTTY = true;
  columns = 80;
  rows = 40;

  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", (chunk) => {
      if (typeof chunk === "string") this.frames.push(chunk);
    });
  }

  /** Last frame carrying rendered content — ink emits bare cursor escapes
   *  after the paint, so the literal last chunk is usually `[?25l`. */
  lastFrame(): string {
    for (let index = this.frames.length - 1; index >= 0; index -= 1) {
      const frame = stripAnsi(this.frames[index] ?? "");
      if (frame.trim()) return frame;
    }
    return "";
  }
}

type InkRender = (typeof import("ink"))["render"];
type VList = (typeof import("../shared/VirtualizedList.js"))["VirtualizedList"];

const instances: Array<ReturnType<InkRender>> = [];
let inkRender: InkRender;
let VirtualizedList: VList;
let Box: (typeof import("ink"))["Box"];
let Text: (typeof import("ink"))["Text"];
let Scrollable: (typeof import("../Scrollable.js"))["Scrollable"];
let useScrollViewport: (typeof import("../Scrollable.js"))["useScrollViewport"];
let ScrollProvider: (typeof import("../../contexts/ScrollProvider.js"))["ScrollProvider"];
let MouseProvider: (typeof import("../../contexts/MouseContext.js"))["MouseProvider"];

beforeAll(async () => {
  const ink = await import("ink");
  inkRender = ink.render;
  Box = ink.Box;
  Text = ink.Text;
  ({ VirtualizedList } = await import("../shared/VirtualizedList.js"));
  ({ Scrollable, useScrollViewport } = await import("../Scrollable.js"));
  ({ ScrollProvider } = await import("../../contexts/ScrollProvider.js"));
  ({ MouseProvider } = await import("../../contexts/MouseContext.js"));
});

afterEach(() => {
  while (instances.length) {
    const instance = instances.pop();
    instance?.unmount();
    instance?.cleanup();
  }
});

/** Long enough for ResizeObserver to fire and the measure flush to land.
 *  The measure→offsets→re-measure loop settles in ~4 passes. */
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 120));

interface Row {
  key: string;
  lines: number;
}

const rows = (count: number, lines: number): Row[] =>
  Array.from({ length: count }, (_, index) => ({
    key: `item-${index}`,
    lines,
  }));

const renderRow = (row: Row) => (
  <Box flexDirection="column">
    {Array.from({ length: row.lines }, (_, line) => (
      <Text key={line}>
        {row.key}
        {line === 0 ? "-head" : `-line${line}`}
      </Text>
    ))}
  </Box>
);

const mount = async (node: React.ReactElement) => {
  const stdout = new TestStdout();
  const instance = inkRender(node, {
    stdout: stdout as unknown as NodeJS.WriteStream,
    debug: true,
    exitOnCtrlC: false,
    patchConsole: false,
  });
  instances.push(instance);
  await settle();
  return stdout.lastFrame();
};

// ---------------------------------------------------------------------
// VirtualizedList in isolation — which rows land in the slice.
//
// Rendered into a tall plain Box, NOT a scroll container: painting the
// slice at the right offset is the parent's job, and mixing the two in
// here would make these assertions test ink's scrolling instead.
// ---------------------------------------------------------------------

const renderList = async (props: {
  data: Row[];
  containerHeight: number;
  scrollTop: number;
  estimatedItemHeight?: number;
}) =>
  mount(
    <Box flexDirection="column">
      <VirtualizedList<Row>
        data={props.data}
        keyExtractor={(row) => row.key}
        estimatedItemHeight={props.estimatedItemHeight ?? 1}
        containerHeight={props.containerHeight}
        scrollTop={props.scrollTop}
        width={80}
        renderItem={renderRow}
      />
    </Box>,
  );

describe("VirtualizedList slice", () => {
  it("never yields an empty slice, even with scrollTop past the end", async () => {
    // The blank-screen case. Before the clamp, the binary search returned
    // first = data.length against a scrollTop past the offset table, so
    // slice(first, last + 1) was empty and the list emitted a bare spacer.
    const frame = await renderList({
      data: rows(12, 1),
      containerHeight: 10,
      scrollTop: 500,
    });

    expect(frame).toContain("item-11");
  });

  it("keeps a single tall row rendered when scrolled past it", async () => {
    const frame = await renderList({
      data: [
        { key: "item-0", lines: 2 },
        { key: "item-1", lines: 40 },
      ],
      containerHeight: 10,
      scrollTop: 300,
    });

    expect(frame).toContain("item-1-head");
  });

  it("folds measured heights into the offsets instead of holding estimates", async () => {
    // Every row is 5 lines tall but estimated at 1. Once measured, the
    // viewport (10 rows + 5 overscan) reaches only into item-2. If the
    // offsets memo never sees the measurements it keeps the 1-row estimate
    // and rows out to item-14 stay in the slice — the stale-offsets
    // signature, and what put scrollTop out of range to begin with.
    const frame = await renderList({
      data: rows(20, 5),
      containerHeight: 10,
      scrollTop: 0,
      estimatedItemHeight: 1,
    });

    expect(frame).toContain("item-0-head");
    expect(frame).not.toContain("item-10-head");
  });
});

// ---------------------------------------------------------------------
// The real loop: Scrollable measures, pins to bottom, and feeds scrollTop
// back into the list that produced the content it measured.
// ---------------------------------------------------------------------

/** Mirrors MessageListOptimized's contract with the viewport context. */
const ListInScrollable: React.FC<{ data: Row[] }> = ({ data }) => {
  const viewport = useScrollViewport();
  if (!viewport || viewport.innerHeight <= 0) {
    return (
      <Box flexDirection="column">
        {data.map((row) => (
          <Box key={row.key}>{renderRow(row)}</Box>
        ))}
      </Box>
    );
  }
  return (
    <VirtualizedList<Row>
      data={data}
      keyExtractor={(row) => row.key}
      estimatedItemHeight={2}
      containerHeight={viewport.innerHeight}
      scrollTop={viewport.scrollTop}
      width={80}
      renderItem={renderRow}
    />
  );
};

const renderScrollable = async (data: Row[], height: number) =>
  mount(
    // hasFocus={false}: focus only gates keyboard/mouse routing, and the
    // keypress fallback would demand raw mode on a non-TTY test stdin.
    <MouseProvider>
      <ScrollProvider>
        <Scrollable hasFocus={false} maxHeight={height} flexGrow={1}>
          <ListInScrollable data={data} />
        </Scrollable>
      </ScrollProvider>
    </MouseProvider>,
  );

describe("Scrollable + VirtualizedList", () => {
  it("shows the tail of a long message instead of blanking", async () => {
    // The reported bug: a message long enough to scroll blanked the screen.
    // Pin-to-bottom must land on the end of the tall row, and the frame
    // must carry its last lines.
    const data = [...rows(11, 2), { key: "item-11", lines: 40 }];
    const frame = await renderScrollable(data, 20);

    expect(frame.trim()).not.toBe("");
    expect(frame).toContain("item-11-line39");
  });

  it("does not blank when virtualization engages on an unmeasured transcript", async () => {
    // Cold start: Scrollable measures the non-virtualized first paint, so
    // virtualization switches on with scrollTop already pinned to the
    // bottom of a transcript whose rows have never been measured. Every
    // offset is an estimate, so scrollTop starts far out of range and only
    // the clamp can walk it back.
    const data = Array.from({ length: 60 }, (_, index) => ({
      key: `item-${index}`,
      lines: index % 7 === 6 ? 12 : 2,
    }));
    const frame = await renderScrollable(data, 20);
    const painted = frame
      .split("\n")
      .filter((line) => line.trim().length > 0).length;

    expect(frame).toContain("item-59");
    // Not just non-blank: a converged viewport is *full*. Pre-fix this
    // frame was a handful of rows stranded above empty space, which a
    // bare non-empty assertion would have waved through.
    expect(painted).toBeGreaterThanOrEqual(18);
  });
});
