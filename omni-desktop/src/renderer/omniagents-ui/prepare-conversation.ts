import { getConversationDraft, updateConversationDraft } from './conversation-drafts';
import type { RPCClient } from './rpc/client';
import { ModelCatalogClient } from './rpc/model-catalog';

/** Apply pre-launch choices before accepting a prompt. Session setters create
 * the server session themselves; session.ensure is not a readiness probe. */
export async function prepareConversation(client: RPCClient, sessionId: string) {
  const draft = getConversationDraft(sessionId);
  const catalog = new ModelCatalogClient(client);
  const requireOk = (result: { ok?: boolean; reason?: string }) => {
    if (!result.ok) {
      throw new Error(result.reason ?? 'Could not apply conversation settings. Please retry.');
    }
  };
  let providerChanged = false;
  if (draft.model) {
    let result = await catalog.setSessionModel(sessionId, draft.model);
    if (!result.ok && result.reasons?.some((reason) => reason.code === 'provider_mismatch')) {
      // The workspace switch was confirmed separately. The session catalog
      // still refuses migration of existing history without that confirmation.
      const current = await catalog.listModels({ sessionId });
      if (current.default_model) {
        result = await catalog.setSessionModel(sessionId, current.default_model);
        providerChanged = result.ok;
      }
    }
    requireOk(result);
  }
  if (draft.reasoning && !providerChanged) {
    requireOk(await catalog.setSessionReasoning(sessionId, draft.reasoning));
  }
  if (draft.approvals) {
    requireOk(await client.setSessionApprovals(sessionId, draft.approvals));
  }
  if (draft.workflow) {
    requireOk(await client.setSessionWorkflow(sessionId, draft.workflow));
  }
  updateConversationDraft(sessionId, {
    model: undefined,
    reasoning: undefined,
    approvals: undefined,
    workflow: undefined,
  });
}
