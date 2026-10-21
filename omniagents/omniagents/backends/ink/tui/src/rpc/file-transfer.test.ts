import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import { downloadFile } from "./file-transfer.js";
import type { RPCClient } from "./client.js";

function fixture(text = "hello") {
  const bytes = Buffer.from(text);
  const api = {
    fsDownloadOpen: vi.fn().mockResolvedValue({
      transfer_id: "transfer",
      size: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex"),
    }),
    fsDownloadRead: vi
      .fn()
      .mockImplementation(async (_env, _id, { offset, length }) => {
        const chunk = bytes.subarray(offset, offset + length);
        return {
          offset,
          bytes: chunk.length,
          data: chunk.toString("base64"),
          eof: offset + chunk.length === bytes.length,
        };
      }),
    fsDownloadClose: vi.fn().mockResolvedValue(true),
  };
  return { api, client: api as unknown as RPCClient };
}
describe("file transfers", () => {
  it("passes unusual paths literally and bounds preview reads", async () => {
    const { api, client } = fixture("abcdef");
    const path = "folder space/雪 '$(touch nope);*.txt";
    expect(
      (await downloadFile(client, "env", path, 3, true)).bytes.toString(),
    ).toBe("abc");
    expect(api.fsDownloadOpen).toHaveBeenCalledWith("env", path);
    expect(api.fsDownloadRead).toHaveBeenCalledWith("env", "transfer", {
      offset: 0,
      length: 3,
    });
    expect(api.fsDownloadClose).toHaveBeenCalledOnce();
  });
  it.each([
    "size",
    "read",
    "zero",
    "offset",
    "truncated",
    "checksum",
    "cancelled",
  ])("releases the handle on %s failure", async (kind) => {
    const { api, client } = fixture();
    if (kind === "read")
      api.fsDownloadRead.mockRejectedValue(new Error("disconnected"));
    if (["zero", "offset", "truncated"].includes(kind))
      api.fsDownloadRead.mockResolvedValue({
        offset: kind === "offset" ? 1 : 0,
        bytes: kind === "zero" ? 0 : 1,
        data: kind === "zero" ? "" : "aA==",
        eof: true,
      });
    if (kind === "checksum")
      api.fsDownloadRead.mockResolvedValue({
        offset: 0,
        bytes: 5,
        data: Buffer.from("wrong").toString("base64"),
        eof: true,
      });
    await expect(
      downloadFile(
        client,
        "env",
        "file",
        kind === "size" ? 1 : 100,
        false,
        () => kind !== "cancelled",
      ),
    ).rejects.toThrow();
    expect(api.fsDownloadClose).toHaveBeenCalledOnce();
  });
  it("does not let a cleanup failure replace a successful read", async () => {
    const { api, client } = fixture();
    api.fsDownloadClose.mockRejectedValue(new Error("disconnected"));
    expect(
      (await downloadFile(client, "env", "file", 100)).bytes.toString(),
    ).toBe("hello");
  });
});

import { uploadFile } from "./file-transfer.js";
import { ConnectionClosedError } from "./lifecycle.js";

it.each(["chunk", "cancel", "invalid-size"])(
  "aborts an upload on %s without committing",
  async (kind) => {
    const api = {
      fsUploadOpen: vi.fn().mockResolvedValue({
        transfer_id: "upload",
        chunk_size: kind === "invalid-size" ? Infinity : 2,
      }),
      fsUploadChunk: vi.fn().mockRejectedValue(new Error("disconnected")),
      fsUploadCommit: vi.fn(),
      fsUploadAbort: vi.fn().mockResolvedValue(true),
    };
    await expect(
      uploadFile(
        api as unknown as RPCClient,
        "env",
        "file",
        Buffer.from("hello"),
        "old",
        () => kind !== "cancel",
      ),
    ).rejects.toThrow();
    expect(api.fsUploadCommit).not.toHaveBeenCalled();
    expect(api.fsUploadAbort).toHaveBeenCalledWith("env", "upload");
  },
);

it.each([true, false])(
  "verifies a lost commit by reading, without retrying it (content matches: %s)",
  async (matches) => {
    const { api, client } = fixture(matches ? "saved" : "other");
    const write = {
      fsUploadOpen: vi
        .fn()
        .mockResolvedValue({ transfer_id: "upload", chunk_size: 10 }),
      fsUploadChunk: vi.fn().mockResolvedValue({}),
      fsUploadCommit: vi
        .fn()
        .mockRejectedValue(new ConnectionClosedError("lost")),
      fsUploadAbort: vi.fn(),
      waitUntilReady: vi.fn().mockResolvedValue(undefined),
    };
    Object.assign(api, write);
    const result = uploadFile(
      client,
      "env",
      "file",
      Buffer.from("saved"),
      "old",
    );
    if (matches)
      await expect(result).resolves.toEqual({
        kind: "saved",
        sha: createHash("sha256").update("saved").digest("hex"),
      });
    else
      await expect(result).resolves.toEqual({
        kind: "reloaded",
        sha: createHash("sha256").update("other").digest("hex"),
        bytes: Buffer.from("other"),
      });
    expect(write.fsUploadCommit).toHaveBeenCalledOnce();
    expect(write.fsUploadAbort).not.toHaveBeenCalled();
  },
);

it("surfaces the transport error when a lost commit cannot be verified", async () => {
  const { api, client } = fixture("saved");
  Object.assign(api, {
    fsUploadOpen: vi
      .fn()
      .mockResolvedValue({ transfer_id: "upload", chunk_size: 10 }),
    fsUploadChunk: vi.fn().mockResolvedValue({}),
    fsUploadCommit: vi
      .fn()
      .mockRejectedValue(new ConnectionClosedError("lost reply")),
    fsUploadAbort: vi.fn(),
    waitUntilReady: vi.fn().mockRejectedValue(new Error("no reconnect")),
  });
  await expect(
    uploadFile(client, "env", "file", Buffer.from("saved"), "old"),
  ).rejects.toThrow("lost reply");
  expect(api.fsDownloadOpen).not.toHaveBeenCalled();
});
