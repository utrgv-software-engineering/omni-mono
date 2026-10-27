import type { LayoutMode } from '@/shared/types';

const VALID_LAYOUT_MODES: LayoutMode[] = ['work', 'chat', 'routines', 'plugins', 'settings', 'gallery'];

/**
 * A stored layout mode the app no longer has falls back to Chat. Returns the
 * replacement, or null when the stored mode is valid.
 */
export function normalizeLayoutMode(mode: string): LayoutMode | null {
  return VALID_LAYOUT_MODES.includes(mode as LayoutMode) ? null : 'chat';
}
