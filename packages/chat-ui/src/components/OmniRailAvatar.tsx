import { useEffect, useRef } from 'react';
import { OmniPlanet } from './omni-avatar/planet';

/** The idle rail identity; unmounting releases its animation and listeners. */
export function OmniRailAvatar() {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const planet = new OmniPlanet(canvas.current!, { size: 56, mood: 'idle' });
    const pause = () => planet.setPaused(document.hidden);
    pause();
    document.addEventListener('visibilitychange', pause);
    return () => {
      document.removeEventListener('visibilitychange', pause);
      planet.dispose();
    };
  }, []);
  return <canvas ref={canvas} aria-hidden="true" className="block size-14" />;
}
