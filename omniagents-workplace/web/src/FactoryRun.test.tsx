import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { FactoryRun } from './FactoryRun';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const { renderChat, connection } = vi.hoisted(() => ({ renderChat: vi.fn(), connection: vi.fn() }));
vi.mock('@omniagents/chat-ui/App', () => ({ App: (props: unknown) => { renderChat(props); return null; } }));
vi.mock('@omniagents/chat-ui/host', () => ({ ChatHostProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock('@omniagents/chat-ui/rpc-context', () => ({ RPCClientProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock('@omniagents/chat-ui/ui-config', () => ({ UiConfigProvider: ({ children, connection: config }: { children: ReactNode; connection: unknown }) => { connection(config); return children; } }));
vi.mock('./api', () => ({ factoryRun: { get: async () => ({ id: 9, automation: 'nightly-review', agent: 'improver', started_at: 1, provider: 'schedule', event: 'cron_fired', pull_requests: [], sessions: [{ agent: 'improver', session_id: 'root' }, { agent: 'worker', session_id: 'child' }] }) } }));

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.clearAllMocks();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  window.history.replaceState(null, '', '/');
});

it('opens the requested question session but keeps the transcript read-only', async () => {
  window.history.replaceState(null, '', '/factory/runs/9?session=child');
  await act(async () => root.render(<FactoryRun runId={9} navigate={vi.fn()} />));
  expect(renderChat).toHaveBeenLastCalledWith(expect.objectContaining({ sessionId: 'child', readOnly: true, allowQuestionResponses: true }));
  expect(connection).toHaveBeenLastCalledWith(expect.objectContaining({ baseUrl: expect.stringContaining('/factory/runs/9/runtime/agents/improver/') }));
});

it('does not select a session outside the run', async () => {
  window.history.replaceState(null, '', '/factory/runs/9?session=private-chat');
  await act(async () => root.render(<FactoryRun runId={9} navigate={vi.fn()} />));
  expect(renderChat).toHaveBeenLastCalledWith(expect.objectContaining({ sessionId: 'root', readOnly: true }));
});
