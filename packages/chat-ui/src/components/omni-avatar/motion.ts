// Small browser adapter for the original Omni planet renderer.
export const reducedMotion = () =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
export const useMotion = {
  subscribe(listener: (state: { on: boolean }, previous: { on: boolean }) => void) {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    let previous = { on: query.matches };
    const update = () => {
      const next = { on: query.matches };
      listener(next, previous);
      previous = next;
    };
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  },
};
