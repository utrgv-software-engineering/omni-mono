import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { createActor } from 'xstate';
import { voiceSessionMachine } from '@omniagents/client/machines/voice-session.machine';
import { OmniAvatar, avatarMood } from './OmniAvatar';
const { planet } = vi.hoisted(() => ({ planet: { update: vi.fn(), setPaused: vi.fn(), dispose: vi.fn() } }));
vi.mock('./planet', () => ({ OmniPlanet: vi.fn(() => planet) }));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.clearAllMocks();
});
it('prioritizes offline, attention and voice over background work', () => {
  expect(avatarMood(false, false, true, 'speaking')).toBe('asleep');
  expect(avatarMood(true, true, true, 'speaking')).toBe('waiting');
  expect(avatarMood(true, false, true, 'speaking')).toBe('speaking');
  expect(avatarMood(true, false, true, 'attending')).toBe('listening');
  expect(avatarMood(true, false, true, 'closed')).toBe('thinking');
});
it('pauses offscreen, reads live levels, respects reduced motion and disposes', () => {
  vi.useFakeTimers();
  let reduced = false,
    observe!: (entries: { isIntersecting: boolean }[]) => void;
  const disconnect = vi.fn();
  vi.stubGlobal(
    'IntersectionObserver',
    vi.fn((callback) => {
      observe = callback;
      return { observe: vi.fn(), disconnect };
    })
  );
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: reduced }))
  );
  const actor = createActor(voiceSessionMachine).start();
  actor.send({ type: 'OPEN', sessionId: 's' });
  actor.send({ type: 'CONNECTED' });
  actor.send({ type: 'SESSION_STARTED', sessionId: 's', runId: 'r', at: 0 });
  const voice = { actor, levels: { input: 0.6, output: 0 } } as any;
  const container = document.createElement('div'),
    root = createRoot(container);
  act(() => root.render(<OmniAvatar online waiting={false} working voice={voice} />));
  expect(container.querySelector('[role="img"]')?.getAttribute('aria-label')).toBe('Omni · listening');
  expect(planet.update).toHaveBeenLastCalledWith({ mood: 'listening', level: 0.6 });
  act(() => observe([{ isIntersecting: false }]));
  expect(planet.setPaused).toHaveBeenLastCalledWith(true);
  const calls = planet.update.mock.calls.length;
  act(() => vi.advanceTimersByTime(500));
  expect(planet.update).toHaveBeenCalledTimes(calls);
  reduced = true;
  act(() => observe([{ isIntersecting: true }]));
  expect(planet.update).toHaveBeenLastCalledWith({ mood: 'listening', level: 0 });
  act(() => root.unmount());
  expect(planet.dispose).toHaveBeenCalledOnce();
  expect(disconnect).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
  actor.stop();
});
