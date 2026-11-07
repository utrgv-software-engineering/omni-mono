import { EscalationBanner } from '@omniagents/chat-ui/components/EscalationBanner';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let container: HTMLDivElement;
let root: Root;
const escalation = { request_id: 'question-1', message: 'Which environment?' };

beforeEach(() => {
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

it('only renders response controls when explicitly enabled', async () => {
  await act(async () => root.render(<EscalationBanner escalation={escalation} />));
  expect(container.querySelector('input')).toBeNull();
  await act(async () => root.render(<EscalationBanner escalation={null} onRespond={vi.fn()} />));
  expect(container.textContent).toBe('');
});

it('answers a pending client tool without a transcript composer and retains failed answers', async () => {
  const respond = vi.fn().mockRejectedValueOnce(new Error('Question expired')).mockResolvedValueOnce(true);
  await act(async () => root.render(<EscalationBanner escalation={escalation} onRespond={respond} />));
  const input = container.querySelector('input')!;
  expect(container.querySelector('button')!.disabled).toBe(true);
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Staging');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => container.querySelector('button')!.click());
  expect(respond).toHaveBeenCalledWith('Staging');
  expect(container.querySelector('[role="alert"]')!.textContent).toBe('Question expired');
  expect(input.value).toBe('Staging');
  await act(async () => container.querySelector('button')!.click());
  expect(container.querySelector('button')!.disabled).toBe(true);
  expect(respond).toHaveBeenCalledTimes(2);
});
