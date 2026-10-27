import { useEffect, useRef } from 'react';
import { voicePhase } from '@omniagents/client/machines/voice-session.machine';
import type { RealtimeVoice } from '../../hooks/use-realtime-voice';
import { OmniPlanet, type OmniMood } from './planet';
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

/** Presentation only: reads the same chat and voice state as the composer. */
export function OmniAvatar({
  online,
  waiting,
  working,
  voice,
}: {
  online: boolean;
  waiting: boolean;
  working: boolean;
  voice: RealtimeVoice;
}) {
  const node = useRef<HTMLSpanElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const state = useRef({ online, waiting, working, voice });
  state.current = { online, waiting, working, voice };
  useEffect(() => {
    const element = node.current!,
      surface = canvas.current!;
    const planet = new OmniPlanet(surface, { size: 104, mood: 'idle' });
    let visible = true,
      previousMood = '',
      previousLevel = -1;
    const paint = () => {
      if (!visible || document.hidden) return;
      const { online, waiting, working, voice } = state.current;
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
      if (mood !== previousMood || level !== previousLevel) {
        planet.update({ mood, level });
        previousMood = mood;
        previousLevel = level;
        element.dataset.mood = mood;
        element.setAttribute(
          'aria-label',
          `Omni · ${mood === 'asleep' ? 'offline' : mood === 'thinking' ? 'working' : mood}`
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
  }, []);
  return (
    <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex flex-col items-center">
      <span ref={node} role="img" aria-label="Omni" className="block size-26">
        <canvas ref={canvas} aria-hidden="true" className="block size-full" />
      </span>
      <span className="relative -mt-[22px] rounded-full bg-foreground px-3 py-[3px] text-sm leading-5 font-medium text-background">Omni</span>
    </div>
  );
}
