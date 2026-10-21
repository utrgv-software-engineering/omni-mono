import type { RpcMethodMap } from '@omniagents/protocol/gui-v2';

import {
  ConversationOrganizationClient,
  type OrganizationThread,
  type ThreadStatus,
} from '@/renderer/omniagents-ui/rpc/conversation-organization';

export type CanonicalSessionSummary = {
  id: string;
  created_at: string;
  archived: boolean;
  message_count: number;
  title?: string;
  pinned?: boolean;
  searchPreview?: string;
  first_message?: unknown;
  last_message?: unknown;
};

type CanonicalSessionListMethod =
  | 'get_thread'
  | 'list_threads'
  | 'search_threads'
  | 'update_thread'
  | 'list_thread_descendants'
  | 'fork_session'
  | 'export_thread'
  | 'purge_threads';

export interface CanonicalSessionListTransport {
  request<Method extends CanonicalSessionListMethod>(
    method: Method,
    params: RpcMethodMap[Method]['params']
  ): Promise<RpcMethodMap[Method]['result']>;
  on<Event extends 'thread_updated'>(
    event: Event,
    handler: (payload: import('@omniagents/protocol/gui-v2').RpcNotificationMap[Event]) => void
  ): () => void;
}

function timestamp(seconds: number): string {
  return new Date(seconds * 1000).toISOString();
}

/** Preserve the current sidebar contract while making canonical threads authoritative. */
export function threadToSessionSummary(thread: OrganizationThread): CanonicalSessionSummary {
  return {
    id: thread.thread_id,
    created_at: timestamp(thread.created_at),
    archived: thread.status === 'archived',
    message_count: thread.item_count,
    ...(thread.title ? { title: thread.title } : {}),
    pinned: thread.pinned,
    ...(thread.title ? { first_message: { content: thread.title } } : {}),
    last_message: { timestamp: timestamp(thread.updated_at) },
  };
}

/** Load the sidebar's conversation list from the runtime's thread index. */
export async function loadCanonicalSessionList(
  transport: CanonicalSessionListTransport,
  limit: number,
  options: {
    status?: ThreadStatus;
    organization?: ConversationOrganizationClient;
  } = {}
): Promise<CanonicalSessionSummary[]> {
  const organization = options.organization ?? new ConversationOrganizationClient(transport);
  try {
    const page = await organization.listThreads({
      ...(options.status ? { status: options.status } : {}),
      limit,
      order: 'desc',
    });
    return page.threads.map(threadToSessionSummary);
  } finally {
    if (!options.organization) {
      organization.dispose();
    }
  }
}
