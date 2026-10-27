/**
 * Terminals per scope (a chat column, a session), each an xterm instance that
 * outlives the views showing it: hiding and re-showing a terminal reparents
 * the same xterm, and a host whose shells outlive the page (the desktop holds
 * them in its main process) hands them back through `list` after a reload.
 *
 * The transport is the host's: the desktop forwards to its main process, a
 * browser talks to the runtime's terminal socket directly.
 */
import { XTERM_OPTIONS } from '@omniagents/chat-ui/terminal/xterm-options';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import { atom } from 'nanostores';

export type TerminalTransport = {
  /** Terminals that already exist for `scope`. */
  list(scope: string): Promise<string[]>;
  /** Open a new terminal for `scope`; resolves with its id. */
  create(scope: string): Promise<string>;
  dispose(id: string): Promise<void>;
  write(id: string, data: string): void;
  resize(id: string, cols: number, rows: number): void;
  /** Output and exits for every terminal this transport opened. */
  subscribe(handlers: {
    output(scope: string, id: string, data: string): void;
    exited(scope: string, id: string, code: number): void;
  }): () => void;
  /** Turns a failed `create` into what the panel says. */
  describeError?(error: unknown): TerminalCreateError;
};

/** Why a terminal could not open: the session is not running yet, or the runtime refused. */
export type TerminalCreateError = { kind: 'not_ready' } | { kind: 'unavailable'; message: string };

type BaseTerminal = {
  id: string;
  scope: string;
  xterm: Terminal;
  fitAddon: FitAddon;
};

export type TerminalState =
  (BaseTerminal & { isRunning: true }) | (BaseTerminal & { isRunning: false; exitCode: number });

export type TerminalStore = ReturnType<typeof createTerminalStore>;

export function createTerminalStore(transport: TerminalTransport) {
  const $terminalsByScope = atom<Record<string, TerminalState[]>>({});
  const $activeByScope = atom<Record<string, string | null>>({});
  /** The last failed `create` per scope, cleared by the next success. */
  const $createErrorByScope = atom<Record<string, TerminalCreateError | null>>({});
  const hydrated = new Set<string>();
  const hydrating = new Map<string, Promise<void>>();

  const terminalsOf = (scope: string) => $terminalsByScope.get()[scope] ?? [];
  const setTerminals = (scope: string, terminals: TerminalState[]) =>
    $terminalsByScope.set({ ...$terminalsByScope.get(), [scope]: terminals });
  const find = (scope: string, id: string) => terminalsOf(scope).find((terminal) => terminal.id === id) ?? null;

  const setActive = (scope: string, id: string | null): void => {
    const current = $activeByScope.get();
    if (current[scope] !== id) {
      $activeByScope.set({ ...current, [scope]: id });
    }
  };

  const setCreateError = (scope: string, error: TerminalCreateError | null): void => {
    const current = $createErrorByScope.get();
    if (error === null && !(scope in current)) {
      return;
    }
    const next = { ...current };
    if (error === null) {
      delete next[scope];
    } else {
      next[scope] = error;
    }
    $createErrorByScope.set(next);
  };

  const build = (scope: string, id: string): TerminalState => {
    const xterm = new Terminal({ ...XTERM_OPTIONS, cursorBlink: true });
    xterm.onData((data) => transport.write(id, data));
    xterm.onResize(({ cols, rows }) => transport.resize(id, cols, rows));
    const fitAddon = new FitAddon();
    xterm.loadAddon(fitAddon);
    return { id, scope, isRunning: true, xterm, fitAddon };
  };

  /** Adopt the scope's existing terminals, keeping any xterm already open. */
  const hydrate = async (scope: string): Promise<void> => {
    if (hydrated.has(scope)) {
      return;
    }
    const pending = hydrating.get(scope);
    if (pending) {
      return pending;
    }
    const promise = (async () => {
      const ids = await transport.list(scope);
      const existing = terminalsOf(scope);
      const known = new Set(existing.map((terminal) => terminal.id));
      const added = ids.filter((id) => !known.has(id)).map((id) => build(scope, id));
      if (added.length > 0) {
        setTerminals(scope, [...existing, ...added]);
        if (($activeByScope.get()[scope] ?? null) === null) {
          setActive(scope, added[0]!.id);
        }
      }
      hydrated.add(scope);
    })();
    hydrating.set(scope, promise);
    try {
      await promise;
    } finally {
      hydrating.delete(scope);
    }
  };

  const create = async (scope: string): Promise<string> => {
    try {
      const id = await transport.create(scope);
      setCreateError(scope, null);
      setTerminals(scope, [...terminalsOf(scope), build(scope, id)]);
      setActive(scope, id);
      return id;
    } catch (error) {
      setCreateError(
        scope,
        transport.describeError?.(error) ?? {
          kind: 'unavailable',
          message: error instanceof Error ? error.message : String(error),
        }
      );
      throw error;
    }
  };

  /**
   * The first time a scope's terminals are shown, land in a working shell: adopt
   * existing terminals, or open one. Later visits leave an emptied panel empty.
   */
  const ensure = async (scope: string): Promise<void> => {
    const firstVisit = !hydrated.has(scope) && !hydrating.has(scope);
    await hydrate(scope);
    if (firstVisit && terminalsOf(scope).length === 0) {
      try {
        await create(scope);
      } catch {
        // create recorded the error; the panel shows it.
      }
    }
  };

  const destroy = async (scope: string, id?: string): Promise<void> => {
    const targetId = id ?? $activeByScope.get()[scope] ?? null;
    const target = targetId ? find(scope, targetId) : null;
    if (!target) {
      return;
    }
    await transport.dispose(target.id);
    target.xterm.dispose();
    const remaining = terminalsOf(scope).filter((terminal) => terminal.id !== target.id);
    setTerminals(scope, remaining);
    if ($activeByScope.get()[scope] === target.id) {
      setActive(scope, remaining.length > 0 ? remaining[remaining.length - 1]!.id : null);
    }
  };

  /** Drop a scope's views without closing its shells (their owner does that). */
  const forget = (scope: string): void => {
    for (const terminal of terminalsOf(scope)) {
      terminal.xterm.dispose();
    }
    const terminals = { ...$terminalsByScope.get() };
    delete terminals[scope];
    $terminalsByScope.set(terminals);
    const active = { ...$activeByScope.get() };
    delete active[scope];
    $activeByScope.set(active);
    hydrated.delete(scope);
  };

  transport.subscribe({
    output(scope, id, data) {
      find(scope, id)?.xterm.write(data);
    },
    exited(scope, id, exitCode) {
      const terminal = find(scope, id);
      if (!terminal) {
        return;
      }
      terminal.xterm.options.disableStdin = true;
      setTerminals(
        scope,
        terminalsOf(scope).map((t) => (t.id === id ? { ...t, isRunning: false, exitCode } : t))
      );
    },
  });

  return {
    $terminalsByScope,
    $activeByScope,
    $createErrorByScope,
    hydrate,
    ensure,
    create,
    destroy,
    forget,
    setActive,
  };
}
