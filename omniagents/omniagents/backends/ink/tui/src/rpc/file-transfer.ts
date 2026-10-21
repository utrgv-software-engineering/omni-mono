import { createHash } from "node:crypto";
import { isReplyLost } from "./lifecycle.js";
import type { RPCClient } from "./client.js";

/** Always release a download, including rejected sizes and malformed/interrupted reads. */
export async function downloadFile(
  client: RPCClient,
  environment: string,
  path: string,
  cap: number,
  preview = false,
  current = () => true,
) {
  const open = await client.fsDownloadOpen(environment, path);
  try {
    if (!current()) throw new Error("file view closed");
    if (!Number.isSafeInteger(open.size) || open.size < 0)
      throw new Error("invalid file size");
    if (!preview && open.size > cap)
      throw new Error(`${path} is too large to edit (limit ${cap} bytes)`);
    const limit = Math.min(open.size, cap);
    const chunks: Buffer[] = [];
    let offset = 0;
    while (offset < limit) {
      const length = Math.min(64 * 1024, limit - offset);
      const chunk = await client.fsDownloadRead(environment, open.transfer_id, {
        offset,
        length,
      });
      if (!current()) throw new Error("file view closed");
      const bytes = Buffer.from(chunk.data, "base64");
      if (
        chunk.offset !== offset ||
        chunk.bytes !== bytes.length ||
        bytes.length === 0 ||
        bytes.length > length
      ) {
        throw new Error("invalid file download chunk");
      }
      chunks.push(bytes);
      offset += bytes.length;
      if (chunk.eof && offset < limit)
        throw new Error("file download ended early");
    }
    const bytes = Buffer.concat(chunks);
    if (
      limit === open.size &&
      createHash("sha256").update(bytes).digest("hex") !== open.sha256
    ) {
      throw new Error("file download checksum mismatch");
    }
    return { bytes, sha: open.sha256, size: open.size };
  } finally {
    // Cleanup must not replace the original error, or stall a successful read on disconnect.
    void client.fsDownloadClose(environment, open.transfer_id).catch(() => {});
  }
}

/**
 * `saved`: the server holds exactly what was uploaded. `reloaded`: the
 * commit's reply was lost and the server's file differs from the upload —
 * `bytes` is what the server actually holds, and it is the truth the editor
 * must show. Callers never see uncertainty; they see server state.
 */
export type UploadOutcome =
  | { kind: "saved"; sha: string }
  | { kind: "reloaded"; sha: string; bytes: Buffer };

/** Never replay a write. A lost commit reply is resolved by reading the file back. */
export async function uploadFile(
  client: RPCClient,
  environment: string,
  path: string,
  bytes: Buffer,
  expectedSha256: string,
  current = () => true,
  reloadCap = bytes.length,
): Promise<UploadOutcome> {
  const sha = createHash("sha256").update(bytes).digest("hex");
  const open = await client.fsUploadOpen(environment, path, bytes.length, {
    sha256: sha,
    expectedSha256,
    overwrite: true,
  });
  let committing = false;
  try {
    const chunkSize = open.chunk_size;
    if (!Number.isSafeInteger(chunkSize) || chunkSize <= 0)
      throw new Error("invalid upload chunk size");
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
      if (!current()) throw new Error("file view closed");
      await client.fsUploadChunk(
        environment,
        open.transfer_id,
        offset,
        bytes.subarray(offset, offset + chunkSize).toString("base64"),
      );
    }
    if (!current()) throw new Error("file view closed");
    committing = true;
    await client.fsUploadCommit(environment, open.transfer_id);
    return { kind: "saved", sha };
  } catch (error) {
    if (!committing) {
      void client.fsUploadAbort(environment, open.transfer_id).catch(() => {});
    } else if (current() && isReplyLost(error)) {
      // The commit may or may not have landed. Read the file back once the
      // connection returns: a matching checksum means it saved; anything
      // else is the server's content, which replaces the draft.
      let saved: Awaited<ReturnType<typeof downloadFile>>;
      try {
        await client.waitUntilReady();
        if (!current()) throw error;
        saved = await downloadFile(
          client,
          environment,
          path,
          Math.max(reloadCap, bytes.length),
          false,
          current,
        );
      } catch {
        // Verification unavailable (no reconnect within the deadline, or
        // the read itself failed): surface the transport's own error and
        // keep the draft so the next save asks the server again.
        throw error;
      }
      if (saved.sha === sha) return { kind: "saved", sha };
      return { kind: "reloaded", sha: saved.sha, bytes: saved.bytes };
    }
    throw error;
  }
}
