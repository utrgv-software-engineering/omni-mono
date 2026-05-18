/**
 * Bridge between the ``escalate`` client-function handler (in
 * useMessagesNew.ts) and the input-submit handler (in App.tsx).
 *
 * Flow:
 * 1. Server sends ``client_request("escalate", {message}, expect_result=true)``.
 * 2. The handler creates a deferred promise, parks the resolver here keyed
 *    by ``request_id``, sets the escalation banner, and returns the promise.
 *    The RPC client awaits the promise before sending ``client_response``.
 * 3. User's next ``handleSubmit`` looks up the resolver, hands back the
 *    typed text, and clears the banner. The agent's blocking ``escalate``
 *    tool call resumes with the reply as its result.
 *
 * The map is module-scoped (not React state) because the handler closure
 * captures the resolver synchronously; storing it in a ref would require
 * passing the ref through several component layers.
 */

type EscalationResolver = (reply: { reply: string }) => void;

const resolvers = new Map<string, EscalationResolver>();

export function registerEscalationResolver(
  requestId: string,
  resolve: EscalationResolver,
): void {
  resolvers.set(requestId, resolve);
}

export function resolveEscalation(requestId: string, reply: string): boolean {
  const resolve = resolvers.get(requestId);
  if (!resolve) return false;
  resolvers.delete(requestId);
  resolve({ reply });
  return true;
}

export function discardEscalation(requestId: string): void {
  resolvers.delete(requestId);
}
