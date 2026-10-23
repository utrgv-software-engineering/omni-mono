import { map } from 'nanostores';

import type { ReasoningEffort } from '../rpc/model-catalog';

/**
 * Per-conversation composer state that outlives the composer: unsent text and
 * attachments, and pre-launch model choices. Nothing about in-flight sends
 * lives here; a send either completes or its text comes back to the box.
 * In memory only: a reload starts every composer empty.
 */
export type ConversationDraft = {
  text: string;
  files: File[];
  model?: string;
  reasoning?: ReasoningEffort;
  approvals?: 'user' | 'auto';
  workflow?: 'off' | 'guardian';
};

const empty: ConversationDraft = { text: '', files: [] };
export const conversationDrafts = map<Record<string, ConversationDraft>>({});

export const getConversationDraft = (id: string): ConversationDraft => conversationDrafts.get()[id] ?? empty;

/** Merge `patch` into the conversation's draft. */
export function updateConversationDraft(id: string, patch: Partial<ConversationDraft>) {
  conversationDrafts.setKey(id, { ...getConversationDraft(id), ...patch });
}
