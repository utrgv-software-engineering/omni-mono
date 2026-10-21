import { describe, expect, it } from 'vitest';

import { normalizeLayoutMode } from '@/lib/store-init';

describe('normalizeLayoutMode', () => {
  it('keeps current modes', () => {
    for (const mode of ['work', 'chat', 'routines', 'plugins', 'settings', 'gallery']) {
      expect(normalizeLayoutMode(mode)).toBeNull();
    }
  });

  it('sends anything else to chat', () => {
    expect(normalizeLayoutMode('sandboxes')).toBe('chat');
    expect(normalizeLayoutMode('')).toBe('chat');
  });
});
