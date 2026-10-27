import { DEFAULT_LIFECYCLE_POLICY } from '../lifecycle';

/** A probe makes one attempt: a runtime without voice, or a proxy in front
 * of one, answers the dial with a refusal, not a reason to retry. */
const PROBE_POLICY = { ...DEFAULT_LIFECYCLE_POLICY, reconnectMaxAttempts: 1 };

/** A capability probe owns its temporary socket, including failed connects. */
export async function probeRealtimeCapabilities(url: string, token: string | undefined, signal: AbortSignal) {
  signal.throwIfAborted();
  const { RealtimeRPCClient } = await import('./realtime');
  signal.throwIfAborted();
  const client = new RealtimeRPCClient(url, token, false, PROBE_POLICY);
  let closed = false;
  const close = () => {
    if (!closed) {
      closed = true;
      client.disconnect();
    }
  };
  signal.addEventListener('abort', close, { once: true });
  try {
    await client.connect();
    signal.throwIfAborted();
    const capabilities = await client.capabilities();
    signal.throwIfAborted();
    return !!capabilities?.enabled;
  } finally {
    signal.removeEventListener('abort', close);
    close();
  }
}
