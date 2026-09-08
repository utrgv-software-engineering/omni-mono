import React from "react";
import {
  MultiLineInputOptimized,
  type ComposerDraft,
  type MultiLineInputProps,
} from "./MultiLineInputOptimized.js";

/** App-owned, memory-only drafts survive panel unmounts; sessions and replies are isolated. */
export class ComposerDrafts {
  private nextId = 0;
  private sessions = new Map<string | undefined, Map<string, ComposerDraft>>();
  get(sessionId?: string, replyId = ""): ComposerDraft {
    let replies = this.sessions.get(sessionId);
    if (!replies) this.sessions.set(sessionId, (replies = new Map()));
    let draft = replies.get(replyId);
    if (!draft) replies.set(replyId, (draft = { id: ++this.nextId }));
    const entry = draft;
    const owner = replies;
    draft.release = () => {
      if (
        entry.notify ||
        entry.pending ||
        entry.current?.draft ||
        entry.current?.lines.some((line) => line.length > 0)
      )
        return;
      if (owner.get(replyId) === entry) owner.delete(replyId);
      if (owner.size === 0 && this.sessions.get(sessionId) === owner)
        this.sessions.delete(sessionId);
    };
    return draft;
  }
  adoptSession(sessionId: string): void {
    const drafts = this.sessions.get(undefined);
    if (drafts && !this.sessions.has(sessionId)) {
      this.sessions.set(sessionId, drafts);
      this.sessions.delete(undefined);
    }
  }
}

export function SessionComposer({
  drafts,
  sessionId,
  replyId,
  ...props
}: Omit<MultiLineInputProps, "draft"> & {
  drafts: ComposerDrafts;
  sessionId?: string;
  replyId?: string;
}) {
  const draft = drafts.get(sessionId, replyId);
  return (
    <MultiLineInputOptimized
      key={draft.id}
      {...props}
      draft={draft}
    />
  );
}
