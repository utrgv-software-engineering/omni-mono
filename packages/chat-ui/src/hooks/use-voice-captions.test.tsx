import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { useVoiceCaptions } from './use-voice-captions';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it('defaults off when requested and remembers the CC choice after remount', () => {
  localStorage.removeItem('omniagents.voice-captions');
  const container = document.createElement('div');
  let root = createRoot(container);
  function Test() {
    const [on, change] = useVoiceCaptions(false);
    return (
      <button aria-pressed={on} onClick={() => change(!on)}>
        CC
      </button>
    );
  }
  act(() => root.render(<Test />));
  expect(container.firstElementChild?.getAttribute('aria-pressed')).toBe('false');
  act(() => container.querySelector('button')!.click());
  expect(localStorage.getItem('omniagents.voice-captions')).toBe('true');
  act(() => root.unmount());
  root = createRoot(container);
  act(() => root.render(<Test />));
  expect(container.firstElementChild?.getAttribute('aria-pressed')).toBe('true');
  act(() => root.unmount());
  localStorage.removeItem('omniagents.voice-captions');
});
