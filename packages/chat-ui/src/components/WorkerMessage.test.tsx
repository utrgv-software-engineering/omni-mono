import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { WorkerMessage } from './WorkerMessage';

it('starts collapsed and reveals the complete handoff without automatically opening the transcript', async () => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  const onOpenTranscript = vi.fn();
  try {
    await act(async () => root.render(<WorkerMessage worker={{
      id: 'nyx-123', name: 'nyx', task: 'Design concept C',
      message: 'Full handoff\n/workspace/Concept.dc.html',
    }} onOpenTranscript={onOpenTranscript} />));
    const trigger = container.querySelector('button')!;
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(container.textContent).not.toContain('Full handoff');
    expect(onOpenTranscript).not.toHaveBeenCalled();
    await act(async () => trigger.click());
    expect(container.textContent).toContain('Full handoff\n/workspace/Concept.dc.html');
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'View transcript')!.click());
    expect(onOpenTranscript).toHaveBeenCalledOnce();
    await act(async () => trigger.click());
    expect(container.textContent).not.toContain('Full handoff');
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});
