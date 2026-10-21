import { atom } from 'nanostores';

import { persistedStoreApi } from '@/renderer/services/store';

/**
 * Which routine is open in the Routines surface (null = the list). Same shape as the inbox's `$inboxView`, and for the same reason:
 * one source of truth for "which routine is open" that cross-tab jumps can
 * set before raising the surface.
 */
export const $routinesView = atom<{ selectedTaskId: string | null }>({ selectedTaskId: null });

/** Open the Routines surface, optionally on a specific routine. */
export function goToRoutine(selectedTaskId?: string): void {
  $routinesView.set({ selectedTaskId: selectedTaskId ?? null });
  if (persistedStoreApi.$atom.get().layoutMode !== 'routines') {
    persistedStoreApi.setKey('layoutMode', 'routines');
  }
}
