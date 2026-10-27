import { useCallback, useState } from 'react';
const key = 'omniagents.voice-captions';
export function useVoiceCaptions(defaultEnabled: boolean) {
  const [enabled, setEnabled] = useState(() => {
    try {
      const saved = localStorage.getItem(key);
      return saved === null ? defaultEnabled : saved === 'true';
    } catch {
      return defaultEnabled;
    }
  });
  const change = useCallback((value: boolean) => {
    setEnabled(value);
    try {
      localStorage.setItem(key, String(value));
    } catch {
      /* Private mode: retain the in-memory preference. */
    }
  }, []);
  return [enabled, change] as const;
}
