import { ModelSessionControls } from '@omniagents/chat-ui/components/ModelSessionControls';
import type { ModelCatalogRpcTransport } from '@omniagents/client/rpc/model-catalog';
import { updateConversationDraft } from '@omniagents/client/session/conversation-drafts';

import { useProductManagementSnapshot } from '@/renderer/omniagents-ui/product-management-context';

const noTransport: ModelCatalogRpcTransport = {
  request: async () => {
    throw new Error('Draft settings do not make session requests');
  },
};

export function DraftSessionControls({ sessionId, disabled }: { sessionId: string; disabled?: boolean }) {
  const snapshot = useProductManagementSnapshot();
  const catalog = snapshot.models.data;
  return (
    <div className="px-3 py-1">
      {catalog ? (
        <ModelSessionControls
          sessionId={sessionId}
          transport={noTransport}
          draftCatalog={catalog}
          disabled={disabled}
          approvalsSupported
          workflowSupported
          onSetApprovalsReviewer={async (approvals) => updateConversationDraft(sessionId, { approvals })}
          onSetWorkflowReviewer={async (workflow) => updateConversationDraft(sessionId, { workflow })}
        />
      ) : (
        <span role="status" className="text-xs text-muted-foreground">
          {snapshot.models.error ? 'Model controls unavailable' : 'Loading models…'}
        </span>
      )}
    </div>
  );
}
