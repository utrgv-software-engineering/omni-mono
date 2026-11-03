import { useEffect, useRef } from 'react';
import { voicePhase } from '@omniagents/client/machines/voice-session.machine';
import type { RealtimeVoice } from '../../hooks/use-realtime-voice';
import { OmniPlanet, PLANET_RADIUS, type OmniMood, type Palette } from './planet';
import { reducedMotion } from './motion';

export function avatarMood(online: boolean, waiting: boolean, working: boolean, phase: string): OmniMood {
  if (!online) return 'asleep';
  if (waiting) return 'waiting';
  if (phase === 'speaking') return 'speaking';
  if (phase === 'attending' || phase === 'listening') return 'listening';
  if (
    phase === 'connecting' ||
    phase === 'starting' ||
    phase === 'thinking' ||
    phase === 'responding' ||
    phase === 'muted'
  )
    return 'attentive';
  if (working) return 'thinking';
  return 'idle';
}

/** The canvas's side in CSS pixels (``size-26``). */
const AVATAR_SIZE = 104;

/** Presentation only: reads the same chat and voice state as the composer. */
export function OmniAvatar({
  name = "Omni",
  online,
  waiting,
  working,
  voice,
  onClick,
  palette,
  top,
}: {
  /** Where the planet's visible top edge sits, in pixels: to line up with the
   * other items of a header. The canvas leaves room around the planet for its
   * aura and ring, so it starts above that edge. */
  top?: number;
  /** An agent's own colours (``agentPalette``); the mark's by default. */
  palette?: Palette;
  name?: string;
  online: boolean;
  waiting: boolean;
  working: boolean;
  voice: RealtimeVoice;
  onClick?: () => void;
}) {
  const node = useRef<HTMLSpanElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const state = useRef({ online, waiting, working, voice, name });
  state.current = { online, waiting, working, voice, name };
  useEffect(() => {
    const element = node.current!,
      surface = canvas.current!;
    const planet = new OmniPlanet(surface, { size: 104, mood: 'idle', ...(palette ? { palette } : {}) });
    let visible = true,
      previousMood = '',
      previousName = '',
      previousLevel = -1;
    const paint = () => {
      if (!visible || document.hidden) return;
      const { online, waiting, working, voice, name } = state.current;
      const snapshot = voice.actor.getSnapshot();
      const phase = snapshot.context.muted && snapshot.matches('live') ? 'muted' : voicePhase(snapshot);
      const mood = avatarMood(online, waiting, working, phase);
      const level = reducedMotion()
        ? 0
        : mood === 'speaking'
          ? voice.levels.output
          : mood === 'listening'
            ? voice.levels.input
            : 0;
      if (mood !== previousMood || level !== previousLevel || name !== previousName) {
        planet.update({ mood, level });
        previousMood = mood;
        previousName = name;
        previousLevel = level;
        element.dataset.mood = mood;
        element.setAttribute(
          'aria-label',
          `${name} · ${mood === 'asleep' ? 'offline' : mood === 'thinking' ? 'working' : mood}`
        );
      }
    };
    const pause = () => {
      planet.setPaused(!visible || document.hidden);
      paint();
    };
    const observer = new IntersectionObserver((entries) => {
      visible = entries.some((e) => e.isIntersecting);
      pause();
    });
    observer.observe(element);
    document.addEventListener('visibilitychange', pause);
    pause();
    const timer = setInterval(paint, 100);
    return () => {
      clearInterval(timer);
      observer.disconnect();
      document.removeEventListener('visibilitychange', pause);
      planet.dispose();
    };
    // A new palette (another agent) redraws it; callers memoize it.
  }, [palette]);
  return (
    <div
      className="pointer-events-none absolute inset-x-0 z-10 flex flex-col items-center"
      style={{ top: top === undefined ? 0 : top - AVATAR_SIZE * (0.5 - PLANET_RADIUS) }}
    >
      {onClick && (
        <button
          type="button"
          onClick={onClick}
          aria-label={`Open ${name} activity`}
          className="pointer-events-auto absolute top-0 size-26 rounded-full focus-visible:outline focus-visible:outline-ring"
        />
      )}
      <span ref={node} role="img" aria-label={name} className="block size-26">
        <canvas ref={canvas} aria-hidden="true" className="block size-full" />
      </span>
      <span className="relative -mt-[22px] rounded-full bg-foreground px-3 py-[3px] text-sm leading-5 font-medium text-background">
        {name}
      </span>
    </div>
  );
}
