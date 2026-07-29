import { describe, it, expect } from "vitest";
import {
  ReplayTracker,
  SessionReplayCoordinator,
  type ResumeResult,
} from "./replay.js";

const payload = (seq: number, streamId = "stream-1") => ({
  session_id: "s",
  seq,
  stream_id: streamId,
});

describe("ReplayTracker", () => {
  it("delivers in-order events and drops duplicates", () => {
    const tracker = new ReplayTracker();
    expect(tracker.observe(payload(1))).toBe("deliver");
    expect(tracker.observe(payload(2))).toBe("deliver");
    expect(tracker.observe(payload(2))).toBe("duplicate");
    expect(tracker.observe(payload(1))).toBe("duplicate");
    expect(tracker.lastSeq).toBe(2);
  });

  it("passes transient events (no seq) through without moving the cursor", () => {
    const tracker = new ReplayTracker();
    tracker.observe(payload(3));
    expect(tracker.observe({ session_id: "s", delta: {} })).toBe("deliver");
    expect(tracker.lastSeq).toBe(3);
  });

  it("detects gaps without advancing the cursor", () => {
    const tracker = new ReplayTracker();
    tracker.observe(payload(1));
    expect(tracker.observe(payload(4))).toBe("gap");
    expect(tracker.lastSeq).toBe(1);
    expect(tracker.gapCount).toBe(1);
  });

  it("adopts the stream mid-flight and flags epoch changes", () => {
    const tracker = new ReplayTracker();
    expect(tracker.observe(payload(41))).toBe("deliver");
    expect(tracker.observe(payload(1, "stream-2"))).toBe("stream_changed");
    expect(tracker.streamId).toBe("stream-2");
    expect(tracker.observe(payload(2, "stream-2"))).toBe("deliver");
  });

  it("applyResume skips applied events and adopts last_seq", () => {
    const tracker = new ReplayTracker();
    tracker.observe(payload(1));
    tracker.observe(payload(2));
    const delivered = tracker.applyResume({
      stream_id: "stream-1",
      last_seq: 6,
      events: [
        { method: "message_output", params: payload(2) },
        { method: "tool_called", params: payload(3) },
        // Non-contiguous (compacted snapshot) inside the window is fine.
        { method: "run_end", params: payload(5) },
      ],
    });
    expect(delivered.map((event) => event.params.seq)).toEqual([3, 5]);
    expect(tracker.lastSeq).toBe(6);
  });
});

describe("SessionReplayCoordinator", () => {
  it("backfills a gap via resume and flushes buffered live events", async () => {
    const delivered: Array<[string, unknown]> = [];
    let resumeCalls = 0;
    const coordinator = new SessionReplayCoordinator(
      async (sessionId, streamId, afterSeq): Promise<ResumeResult> => {
        resumeCalls += 1;
        expect(sessionId).toBe("s");
        expect(streamId).toBe("stream-1");
        expect(afterSeq).toBe(1);
        return {
          session_id: "s",
          stream_id: "stream-1",
          last_seq: 4,
          events: [
            { method: "tool_called", params: payload(2) },
            { method: "tool_result", params: payload(3) },
            { method: "message_output", params: payload(4) },
          ],
        };
      },
      (method, params) => delivered.push([method, (params as { seq?: number }).seq]),
    );

    // seq 1 delivers normally (handle() itself advances the cursor).
    expect(coordinator.handle("run_started", payload(1))).toBe(false);
    // seq 4 arrives next: gap -> handled internally (buffer + resume).
    expect(coordinator.handle("message_output", payload(4))).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(resumeCalls).toBe(1);
    expect(delivered).toEqual([
      ["tool_called", 2],
      ["tool_result", 3],
      ["message_output", 4],
    ]);
    // The buffered live event was deduped after the backfill delivered it.
    expect(coordinator.tracker("s").lastSeq).toBe(4);
  });

  it("reports resync when the resume call fails", async () => {
    const resyncs: string[] = [];
    const coordinator = new SessionReplayCoordinator(
      async () => {
        throw new Error("code -32030: resync required");
      },
      () => {},
      (sessionId) => resyncs.push(sessionId),
    );
    coordinator.tracker("s").observe(payload(1));
    expect(coordinator.handle("message_output", payload(5))).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(resyncs).toEqual(["s"]);
  });

  it("drops duplicate deliveries so no duplicate UI items appear", () => {
    const coordinator = new SessionReplayCoordinator(
      async () => ({}),
      () => {},
    );
    expect(coordinator.handle("message_output", payload(1))).toBe(false);
    expect(coordinator.handle("message_output", payload(1))).toBe(true);
  });
});
