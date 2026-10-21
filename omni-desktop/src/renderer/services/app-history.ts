/**
 * Platform-shell plumbing for top-level navigation (UI/UX gameplan Phase 8).
 *
 * Mirrors the app's navigation state — the active layout mode plus the
 * Projects view — into the browser history, so system back/forward navigate
 * the app instead of exiting it (PWA/browser mode; harmless under Electron).
 * Also keeps `document.title` describing where the user is, for the browser
 * tab, the task switcher, and the Electron window title.
 *
 * Loop guard: applying a popstate writes `lastKey` *before* mutating the
 * stores, so the (async, IPC round-trip) store echo arrives with a key we
 * already recorded and is not pushed again.
 */
import { $projectView, type ProjectView } from '@/renderer/features/Projects/view-state';
import { $initialized, persistedStoreApi } from '@/renderer/services/store';
import type { LayoutMode } from '@/shared/types';

type AppHistoryState = {
  omni: true;
  layoutMode: LayoutMode;
  projectView: ProjectView;
};

const TAB_TITLES: Record<LayoutMode, string> = {
  work: 'Projects',
  chat: 'Chat',
  routines: 'Routines',
  plugins: 'Plugins',
  sandboxes: 'Sandboxes',
  settings: 'Settings',
  gallery: 'Gallery',
};

const currentState = (): AppHistoryState => ({
  omni: true,
  layoutMode: persistedStoreApi.get().layoutMode,
  projectView: $projectView.get(),
});

const keyOf = (state: AppHistoryState): string => JSON.stringify([state.layoutMode, state.projectView]);

/** History entries persist across reloads — an old entry may carry a retired mode. */
const isValidMode = (mode: unknown): mode is LayoutMode => typeof mode === 'string' && mode in TAB_TITLES;

/** History entries persist across app versions — drop shapes that aren't a project view. */
const normalizeProjectView = (view: unknown): ProjectView => {
  const candidate = view as { projectId?: unknown; tab?: unknown } | null;
  if (!candidate || typeof candidate.projectId !== 'string') {
    return null;
  }
  return { projectId: candidate.projectId, tab: candidate.tab === 'settings' ? 'settings' : 'home' };
};

let lastKey = '';
let lastPushAt = 0;

/**
 * One user gesture can land as several store updates milliseconds apart
 * (e.g. a tab click sets layoutMode over IPC *and* resets the Projects view
 * synchronously). Updates inside this window replace the entry instead of
 * stacking, so one gesture costs one Back press.
 */
const COALESCE_MS = 300;

const syncTitle = (mode: LayoutMode): void => {
  document.title = `${TAB_TITLES[mode]} — Omni`;
};

const applyHistoryState = (state: AppHistoryState): void => {
  lastKey = keyOf(state);
  // The next navigation after a back/forward must push, never coalesce —
  // replacing here would overwrite the entry the user just landed on.
  lastPushAt = 0;
  if (persistedStoreApi.get().layoutMode !== state.layoutMode) {
    void persistedStoreApi.setKey('layoutMode', state.layoutMode);
  }
  $projectView.set(normalizeProjectView(state.projectView));
  syncTitle(state.layoutMode);
};

const onNavChange = (): void => {
  const state = currentState();
  const key = keyOf(state);
  if (key === lastKey) {
    return;
  }
  lastKey = key;
  syncTitle(state.layoutMode);
  const now = Date.now();
  try {
    if (now - lastPushAt < COALESCE_MS) {
      window.history.replaceState(state, '');
    } else {
      window.history.pushState(state, '');
    }
    lastPushAt = now;
  } catch {
    // History can throw under rapid-fire updates (Safari rate limit) — the
    // app keeps working, this entry just isn't navigable.
  }
};

let started = false;

/** Idempotent; subscribed from the App shell once the store atom is live. */
export const initAppHistory = (): void => {
  if (started) {
    return;
  }
  started = true;

  const start = (): void => {
    const state = currentState();
    lastKey = keyOf(state);
    syncTitle(state.layoutMode);
    window.history.replaceState(state, '');

    persistedStoreApi.$atom.listen(onNavChange);
    $projectView.listen(onNavChange);

    window.addEventListener('popstate', (event: PopStateEvent) => {
      const state = event.state as Partial<AppHistoryState> | null;
      if (!state?.omni || !isValidMode(state.layoutMode)) {
        return;
      }
      applyHistoryState(state as AppHistoryState);
    });
  };

  if ($initialized.get()) {
    start();
  } else {
    const unsubscribe = $initialized.listen((ready) => {
      if (ready) {
        unsubscribe();
        start();
      }
    });
  }
};
