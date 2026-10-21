import { randomUUID } from "node:crypto";

interface Submission {
  params: Record<string, unknown>;
  pending?: Promise<unknown>;
}

/** Retain unresolved identities across retries; successful sends may be repeated intentionally. */
export class Submissions {
  private outstanding = new Map<string, Submission>();
  constructor(
    private request: (
      method: string,
      params: Record<string, unknown>,
    ) => Promise<unknown>,
    private ready: () => Promise<void>,
  ) {}

  async send<T>(
    method: "start_run" | "enqueue_message",
    params: Record<string, unknown>,
  ): Promise<T> {
    const key = JSON.stringify([method, params]);
    let submission = this.outstanding.get(key);
    const existing = !!submission;
    if (!submission) {
      submission = {
        params: {
          ...JSON.parse(JSON.stringify(params)),
          session_id: params.session_id ?? randomUUID(),
          submission_id: randomUUID(),
        },
      };
      this.outstanding.set(key, submission);
    }
    if (submission.pending) return submission.pending as Promise<T>;
    const record = submission;
    const finish = (result: unknown) => {
      this.outstanding.delete(key);
      return result as T;
    };
    const recover = async (): Promise<T> => {
      await this.ready();
      const state = (await this.request("queue_status", {
        session_id: record.params.session_id,
        submission_id: record.params.submission_id,
      })) as {
        submission?: {
          status: string;
          result?: unknown;
          error?: string;
        } | null;
      };
      if (!("submission" in state))
        throw new Error("Server cannot confirm the submission outcome");
      const receipt = state.submission;
      if (receipt?.status === "completed") return finish(receipt.result);
      if (receipt?.status === "failed") {
        this.outstanding.delete(key);
        throw new Error(receipt.error || "Submission failed");
      }
      if (receipt)
        throw new Error(
          "Submission outcome is unresolved; retry will check the same submission without creating another",
        );
      // A missing receipt can race the original dispatch. The same session and
      // submission id make this safe even if that dispatch subsequently wins.
      return finish(await this.request(method, record.params));
    };
    record.pending = (async () => {
      if (existing) return recover();
      try {
        return finish(await this.request(method, record.params));
      } catch (error) {
        if ((error as { permanent?: boolean })?.permanent) throw error;
        const code = (error as { code?: number })?.code;
        if (
          code !== undefined &&
          [-32700, -32600, -32601, -32602, -32010, -32012, -32013].includes(
            code,
          )
        ) {
          this.outstanding.delete(key);
          throw error;
        }
        return recover();
      }
    })();
    try {
      return (await record.pending) as T;
    } finally {
      record.pending = undefined;
    }
  }
}
