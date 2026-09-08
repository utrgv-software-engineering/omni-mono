import { createHash } from "node:crypto";
import { ConnectionClosedError, RpcTimeoutError } from "./lifecycle.js";
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

/** Never replay a write. An uncertain commit can only be confirmed by reading its checksum. */
export async function uploadFile(
  client: RPCClient,
  environment: string,
  path: string,
  bytes: Buffer,
  expectedSha256: string,
  current = () => true,
): Promise<string> {
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
    return sha;
  } catch (error) {
    if (!committing) {
      void client.fsUploadAbort(environment, open.transfer_id).catch(() => {});
    } else if (
      current() &&
      (error instanceof ConnectionClosedError ||
        error instanceof RpcTimeoutError)
    ) {
      try {
        await client.waitUntilReady();
        if (!current()) throw error;
        const saved = await downloadFile(
          client,
          environment,
          path,
          bytes.length,
          false,
          current,
        );
        if (saved.sha === sha) return sha;
      } catch {
        /* Preserve the draft and the uncertainty if verification is unavailable. */
      }
      throw new Error(
        "Save not confirmed. Your edits are retained; check the file before retrying.",
      );
    }
    throw error;
  }
}
