import { ChatShell, type PendingMessage } from '@omniagents/chat-ui/ChatShell';
import { withChatHost } from '@omniagents/chat-ui/testing/chat-host';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const CHAT_SUGGESTIONS = [
  { label: 'Plan my week', prompt: 'Propose a focused plan for this week.' },
  { label: 'Show me around', prompt: 'Give me a quick tour of what you can do.' },
];

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = withChatHost(createRoot(container));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const renderShell = async (
  overrides: Partial<React.ComponentProps<typeof ChatShell>> = {},
  onSubmit = vi.fn<(message: PendingMessage) => void>()
) => {
  await act(async () => {
    root.render(
      <ChatShell
        greeting="Good morning"
        phase="idle"
        onSubmit={onSubmit}
        suggestions={CHAT_SUGGESTIONS}
        workspaceReady
        onOpenWorkspaceSettings={vi.fn()}
        composerExtras={<button type="button">Attach project</button>}
        {...overrides}
      />
    );
    await Promise.resolve();
  });
  return onSubmit;
};

const buttonNamed = (name: string) => {
  const button = Array.from(container.querySelectorAll('button')).find((candidate) => candidate.textContent === name);
  if (!button) {
    throw new Error(`${name} button not found`);
  }
  return button as HTMLButtonElement;
};

describe('ChatShell lazy launch guidance', () => {
  it('renders helper copy, suggestions, extras, and an enabled composer', async () => {
    await renderShell();

    expect(container.textContent).toContain('Your first message starts a session.');
    expect(buttonNamed('Plan my week')).toBeTruthy();
    expect(buttonNamed('Show me around')).toBeTruthy();
    expect(buttonNamed('Attach project')).toBeTruthy();
    expect(container.querySelector('textarea')?.disabled).toBe(false);
  });

  it('submits a suggestion once with its existing prompt', async () => {
    const onSubmit = await renderShell();

    act(() => buttonNamed('Show me around').click());

    expect(onSubmit).toHaveBeenCalledOnce();
    expect(onSubmit).toHaveBeenCalledWith({ text: CHAT_SUGGESTIONS[1]?.prompt });
  });

  it('gates missing workspace inline and opens settings', async () => {
    const onOpenWorkspaceSettings = vi.fn();
    await renderShell({ workspaceReady: false, onOpenWorkspaceSettings });

    expect(container.textContent).toContain('Choose a workspace folder to start chatting');
    expect(container.textContent).not.toContain('Plan my week');
    expect(container.querySelector('textarea')?.disabled).toBe(true);

    act(() => buttonNamed('Open workspace settings').click());
    expect(onOpenWorkspaceSettings).toHaveBeenCalledOnce();
  });

  it('keeps pending intent beside its starting status', async () => {
    await renderShell({ phase: 'loading', pendingMessages: [{ text: 'Help me plan' }] });

    expect(container.textContent).toContain('Help me plan');
    const status = container.querySelector('[role="status"]');
    expect(status?.textContent).toContain('Starting session…');
  });

  it('keeps pending intent beside an actionable launch error', async () => {
    const onRetry = vi.fn();
    const onSubmit = await renderShell({
      phase: 'error',
      error: 'Sandbox unavailable',
      onRetry,
      pendingMessages: [{ text: 'Help me plan' }],
    });

    expect(container.textContent).toContain('Help me plan');
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Couldn’t start the session');

    act(() => buttonNamed('Retry').click());
    expect(onRetry).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('uses the same error treatment when no message is pending', async () => {
    await renderShell({ phase: 'error', error: 'Sandbox unavailable', onRetry: vi.fn() });

    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Couldn’t start the session');
  });
});
