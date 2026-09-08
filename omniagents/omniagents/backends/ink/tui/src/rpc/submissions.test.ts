import { expect, it, vi } from "vitest";
import { Submissions } from "./submissions.js";

it("checks unresolved receipts on manual retry without sending a second operation", async () => {
  const request = vi
    .fn()
    .mockRejectedValueOnce(new Error("lost response"))
    .mockResolvedValueOnce({ submission: { status: "unknown" } })
    .mockResolvedValueOnce({
      submission: {
        status: "completed",
        result: { run_id: "r", session_id: "s" },
      },
    });
  const submissions = new Submissions(request, async () => {});
  const params = { prompt: "hello", environment_selection: { mode: "none" } };
  await expect(submissions.send("start_run", params)).rejects.toThrow(
    "unresolved",
  );
  await expect(submissions.send("start_run", params)).resolves.toEqual({
    run_id: "r",
    session_id: "s",
  });
  expect(request.mock.calls.map((call) => call[0])).toEqual([
    "start_run",
    "queue_status",
    "queue_status",
  ]);
  expect(request.mock.calls[2][1]).toEqual(request.mock.calls[1][1]);
});

it("resends with identical identities when a receipt is absent, then permits an intentional repeat", async () => {
  const request = vi
    .fn()
    .mockRejectedValueOnce(new Error("lost request"))
    .mockResolvedValueOnce({ submission: null })
    .mockResolvedValue({ ok: true });
  const submissions = new Submissions(request, async () => {});
  const params = { session_id: "s", content: "next" };
  await submissions.send("enqueue_message", params);
  expect(request.mock.calls[2]).toEqual(request.mock.calls[0]);
  await submissions.send("enqueue_message", params);
  expect(request.mock.calls[3][1].submission_id).not.toBe(
    request.mock.calls[0][1].submission_id,
  );
});

it("allows a fresh attempt only after a receipt proves failure", async () => {
  const request = vi
    .fn()
    .mockRejectedValueOnce(new Error("lost response"))
    .mockResolvedValueOnce({
      submission: { status: "failed", error: "invalid environment" },
    })
    .mockResolvedValue({ run_id: "r" });
  const submissions = new Submissions(request, async () => {});
  const params = { session_id: "s", prompt: "hello" };
  await expect(submissions.send("start_run", params)).rejects.toThrow(
    "invalid environment",
  );
  await submissions.send("start_run", params);
  expect(request.mock.calls[2][1].submission_id).not.toBe(
    request.mock.calls[0][1].submission_id,
  );
});

it("retains the identity when receipt lookup fails and never guesses acceptance", async () => {
  const request = vi
    .fn()
    .mockRejectedValueOnce(new Error("lost response"))
    .mockRejectedValueOnce(new Error("lookup disconnected"))
    .mockResolvedValueOnce({ submission: { status: "pending" } });
  const submissions = new Submissions(request, async () => {});
  await expect(
    submissions.send("start_run", { prompt: "hello" }),
  ).rejects.toThrow("lookup disconnected");
  await expect(
    submissions.send("start_run", { prompt: "hello" }),
  ).rejects.toThrow("unresolved");
  expect(request.mock.calls[2]).toEqual(request.mock.calls[1]);
});

it("does not wait for reconnect after an explicit or permanent disconnect", async () => {
  const request = vi
    .fn()
    .mockRejectedValue(Object.assign(new Error("closed"), { permanent: true }));
  const ready = vi.fn();
  const submissions = new Submissions(request, ready);
  await expect(
    submissions.send("start_run", { prompt: "hello" }),
  ).rejects.toThrow("closed");
  expect(ready).not.toHaveBeenCalled();
});
