import { useEffect, useRef } from 'react';
import { agentPalette } from './agent-palette';
import { OmniPlanet, type OmniMood } from './planet';

/** A small, animated face for an agent in a list, in its own colours. */
export function AgentAvatar({
  seed,
  size = 36,
  mood = 'idle',
  className = '',
}: {
  seed: string;
  size?: number;
  mood?: OmniMood;
  className?: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const planet = useRef<OmniPlanet | null>(null);
  useEffect(() => {
    const created = new OmniPlanet(canvas.current!, { size, mood: 'idle', palette: agentPalette(seed) });
    planet.current = created;
    const pause = () => created.setPaused(document.hidden);
    pause();
    document.addEventListener('visibilitychange', pause);
    return () => {
      document.removeEventListener('visibilitychange', pause);
      created.dispose();
      planet.current = null;
    };
  }, [seed, size]);
  useEffect(() => {
    planet.current?.update({ mood });
  }, [mood, seed, size]);
  return (
    <canvas
      ref={canvas}
      aria-hidden="true"
      className={`block shrink-0 ${className}`}
      style={{ width: size, height: size }}
    />
  );
}
