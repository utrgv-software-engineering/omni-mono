import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Box, Text, useStdout } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "../rpc/client.js";
import {
  clampIndex,
  filterRows,
  rowMeta,
  rowsFromSearch,
  rowsFromSessions,
  rowsFromThreads,
  rowTitle,
  sortRows,
  truncate,
  type SessionRow,
} from "./session-manager-model.js";
import { windowStart } from "./model-picker-model.js";
import { Theme } from "../theme.js";

/**
 * The resume screen, promoted from a list to a manager.
 *
 * It reads from whichever surface the server granted at the handshake:
 * ``list_threads``/``search_threads`` where available (titles, pins,
 * fork parentage, live attention), and the stable ``list_sessions``
 * everywhere else. The degraded path is not a different screen — the
 * organize keys simply report themselves unavailable, because a picker
 * that vanishes on an older server is worse than one that explains
 * itself.
 *
 * Every mutation is optimistic-free: the action fires, the list
 * refetches, and the status line reports what happened. A resume screen
 * is visited once per launch, so a round trip costs nothing and a stale
 * pin state costs confusion.
 */

/** How many rows fit between the logo and the help line. */
const CHROME_ROWS = 12;

type Mode = "list" | "search" | "rename" | "confirm";

type PendingConfirm = {
  label: string;
  message: string;
  run: () => Promise<void>;
};

type Props = {
  client: RPCClient | null;
  /** Open a conversation and enter the chat screen. */
  onOpen: (sessionId: string) => void;
  /** Start a fresh conversation. */
  onNew: () => void;
  /**
   * Fired once when the FIRST load finds nothing to resume, so the
   * caller can skip a screen whose only content would be "there is
   * nothing here". Never fired for an empty search result — that is a
   * finding, not an absence.
   */
  onEmpty?: () => void;
};

export const SessionManager = React.memo<Props>(
  ({ client, onOpen, onNew, onEmpty }) => {
    const { stdout } = useStdout();
    const terminalHeight = stdout?.rows || 24;
    const terminalWidth = stdout?.columns || 80;
    const windowSize = Math.max(4, terminalHeight - CHROME_ROWS);

    const [rows, setRows] = useState<SessionRow[]>([]);
    const [index, setIndex] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [statusLine, setStatusLine] = useState<string | null>(null);
    const [mode, setMode] = useState<Mode>("list");
    const [buffer, setBuffer] = useState("");
    const [confirm, setConfirm] = useState<PendingConfirm | null>(null);
    /** The query the CURRENT rows were drawn under, "" for the full list. */
    const [appliedQuery, setAppliedQuery] = useState("");
    const [now, setNow] = useState(() => Date.now());

    // `list_threads` and its siblings are experimental: the server echoes
    // the grant at initialize, so feature-detection is a field read rather
    // than a -32013 round trip.
    //
    // `isGranted` reads a mutable field, not React state, so it is only
    // trustworthy once the handshake has landed. It has: the client
    // assigns `negotiatedInitialization` before emitting `rpc_ready`, and
    // App routes here only after `rpcReady`. Mount this screen earlier
    // than that and every capability below silently reads false.
    const organized = client?.isGranted("list_threads") ?? false;
    const canSearch = client?.isGranted("search_threads") ?? false;
    const canUpdate = client?.isGranted("update_thread") ?? false;
    const canFork = client?.isGranted("fork_session") ?? false;
    const canHold = client?.isGranted("set_session_hold") ?? false;

    // Relative ages go stale on a screen that can sit open; one minute is
    // the coarsest bucket `relativeTime` renders.
    useEffect(() => {
      const timer = setInterval(() => setNow(Date.now()), 60_000);
      return () => clearInterval(timer);
    }, []);

    /**
     * Load the list under `query`. Server-side search when granted;
     * otherwise the full list, filtered locally by the caller — the two
     * are NOT interchangeable (server search reads transcript bodies) and
     * the help line says which one is in play.
     */
    const load = useCallback(
      async (query: string) => {
        if (!client) {
          // No transport: settle into a definite "nothing loaded" rather
          // than an eternal spinner the empty-check would wait behind.
          setLoading(false);
          return;
        }
        setLoading(true);
        setError(null);
        try {
          let next: SessionRow[];
          if (organized && query !== "" && canSearch) {
            // Rank order is the server's; sorting it would discard the ranking.
            next = rowsFromSearch(
              await client.searchThreads(query, { limit: 100 }),
            );
          } else if (organized) {
            next = sortRows(
              rowsFromThreads(await client.listThreads({ limit: 100 })),
            );
            if (query !== "") next = filterRows(next, query);
          } else {
            next = sortRows(rowsFromSessions(await client.listSessions()));
            if (query !== "") next = filterRows(next, query);
          }
          setRows(next);
          setIndex((prev) => clampIndex(prev, next.length));
          setAppliedQuery(query);
        } catch (e) {
          setError((e as Error).message ?? String(e));
        } finally {
          setLoading(false);
        }
      },
      [client, organized, canSearch],
    );

    // Initial load. `load` is stable for a given client/grant set.
    useEffect(() => {
      void load("");
    }, [load]);

    // "Nothing to resume" is a routing decision, and it can only be made
    // once the first unfiltered load has actually answered. Guarded by a
    // ref so a later refetch that happens to empty the list (the user
    // deleted their last conversation) does not yank the screen away.
    const emptyReportedRef = useRef(false);
    useEffect(() => {
      if (emptyReportedRef.current) return;
      if (loading || error !== null) return;
      if (appliedQuery !== "" || rows.length > 0) {
        emptyReportedRef.current = true;
        return;
      }
      emptyReportedRef.current = true;
      onEmpty?.();
    }, [loading, error, appliedQuery, rows.length, onEmpty]);

    // Another client renamed/pinned/archived something: refetch under the
    // query currently applied so the screen never shows a stale title.
    // Held in a ref so the subscription does not churn on every keystroke.
    const appliedQueryRef = useRef(appliedQuery);
    appliedQueryRef.current = appliedQuery;
    useEffect(() => {
      if (!client) return;
      const refetch = () => {
        void load(appliedQueryRef.current);
      };
      client.on("thread_updated", refetch);
      return () => {
        client.off("thread_updated", refetch);
      };
    }, [client, load]);

    const selected = rows[index];

    /**
     * Run a mutation, then refetch. Failures land on the status line
     * rather than replacing the screen: the list is still usable when a
     * single rename was rejected.
     */
    const mutate = useCallback(
      async (label: string, run: () => Promise<unknown>) => {
        try {
          setStatusLine(`${label}…`);
          await run();
          await load(appliedQueryRef.current);
          setStatusLine(label);
        } catch (e) {
          setStatusLine(
            `${label} failed: ${(e as Error).message ?? String(e)}`,
          );
        }
      },
      [load],
    );

    const requireOrganize = useCallback(
      (row: SessionRow | undefined): row is SessionRow => {
        if (!row) return false;
        if (!row.organizable || !canUpdate) {
          setStatusLine(
            "this server does not support organizing conversations",
          );
          return false;
        }
        return true;
      },
      [canUpdate],
    );

    // ----- list-mode keys -------------------------------------------------

    useKeypress(
      (input, key) => {
        // Modified keys are chords for the global layer (quit, panel
        // switching) — never mistake Ctrl+X for the letter x.
        if (key.ctrl || (key.meta && !key.escape)) return false;

        if (key.tab) {
          onNew();
          return true;
        }
        if (key.upArrow || input === "k") {
          setIndex((prev) => (prev > 0 ? prev - 1 : prev));
          return true;
        }
        if (key.downArrow || input === "j") {
          setIndex((prev) => (prev < rows.length - 1 ? prev + 1 : prev));
          return true;
        }
        if (key.return) {
          if (selected) onOpen(selected.id);
          return true;
        }
        if (input === "/") {
          setMode("search");
          setBuffer(appliedQuery);
          return true;
        }
        if (key.escape && appliedQuery !== "") {
          void load("");
          return true;
        }
        if (!selected) return true;

        if (input === "p") {
          if (!requireOrganize(selected)) return true;
          void mutate(selected.pinned ? "unpinned" : "pinned", () =>
            client!.updateThread(selected.id, { pinned: !selected.pinned }),
          );
          return true;
        }
        if (input === "r") {
          if (!requireOrganize(selected)) return true;
          setMode("rename");
          setBuffer(selected.title ?? "");
          return true;
        }
        if (input === "a") {
          if (!requireOrganize(selected)) return true;
          const next = selected.archived ? "active" : "archived";
          void mutate(next === "archived" ? "archived" : "unarchived", () =>
            client!.updateThread(selected.id, { status: next }),
          );
          return true;
        }
        if (input === "f") {
          if (!canFork) {
            setStatusLine("this server does not support forking");
            return true;
          }
          void (async () => {
            try {
              setStatusLine("forking…");
              const result = await client!.forkSession(selected.id);
              const forked = result?.new_session_id;
              if (typeof forked === "string" && forked !== "") {
                // Opening the child is the whole point of the gesture —
                // dropping the user back on the list would make them hunt
                // for a conversation they just created.
                onOpen(forked);
                return;
              }
              setStatusLine("fork returned no session id");
            } catch (e) {
              setStatusLine(
                `fork failed: ${(e as Error).message ?? String(e)}`,
              );
            }
          })();
          return true;
        }
        if (input === "h") {
          if (!canHold) {
            setStatusLine("this server does not support retention holds");
            return true;
          }
          // Deliberately reported as an ACTION, not toggled state: the
          // protocol has no read for the hold flag (no `get_session_hold`
          // RPC, and neither listing returns it), so a badge here would be
          // a guess. Placing the hold is idempotent, so re-pressing is safe.
          void mutate("retention hold placed", () =>
            client!.setSessionHold(selected.id, true),
          );
          return true;
        }
        if (input === "d") {
          setConfirm({
            label: "delete",
            message: `Delete "${rowTitle(selected)}" and its history? This cannot be undone.`,
            run: async () => {
              await client!.deleteSession(selected.id);
            },
          });
          setMode("confirm");
          return true;
        }
        return true; // full-screen: swallow the rest
      },
      { isActive: mode === "list", priority: KeypressPriority.High },
    );

    // ----- search-mode keys ----------------------------------------------

    useKeypress(
      (input, key) => {
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.escape) {
          setMode("list");
          setBuffer("");
          return true;
        }
        if (key.return) {
          setMode("list");
          void load(buffer.trim());
          setBuffer("");
          return true;
        }
        if (key.backspace || key.delete) {
          setBuffer((prev) => prev.slice(0, -1));
          return true;
        }
        if (input) {
          setBuffer((prev) => prev + input);
          return true;
        }
        return true;
      },
      { isActive: mode === "search", priority: KeypressPriority.Critical },
    );

    // ----- rename-mode keys ----------------------------------------------

    useKeypress(
      (input, key) => {
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.escape) {
          setMode("list");
          setBuffer("");
          return true;
        }
        if (key.return) {
          const title = buffer.trim();
          setMode("list");
          setBuffer("");
          if (!selected) return true;
          // An emptied title is a real intent (clear it, falling back to
          // the id), which the protocol spells as an explicit null —
          // omitting the key would mean "leave it alone" instead.
          void mutate(title === "" ? "title cleared" : "renamed", () =>
            client!.updateThread(selected.id, {
              title: title === "" ? null : title,
            }),
          );
          return true;
        }
        if (key.backspace || key.delete) {
          setBuffer((prev) => prev.slice(0, -1));
          return true;
        }
        if (input) {
          setBuffer((prev) => prev + input);
          return true;
        }
        return true;
      },
      { isActive: mode === "rename", priority: KeypressPriority.Critical },
    );

    // ----- confirm-mode keys ---------------------------------------------

    useKeypress(
      (input, key) => {
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (input === "y" && confirm) {
          const pending = confirm;
          setConfirm(null);
          setMode("list");
          void mutate(pending.label, pending.run);
          return true;
        }
        if (key.escape || input === "n") {
          setConfirm(null);
          setMode("list");
          return true;
        }
        return true;
      },
      { isActive: mode === "confirm", priority: KeypressPriority.Critical },
    );

    const start = windowStart(index, rows.length, windowSize);
    const visible = useMemo(
      () => rows.slice(start, start + windowSize),
      [rows, start, windowSize],
    );
    // Title column: leave room for the cursor, badges and the meta tail.
    const titleWidth = Math.max(16, Math.min(48, terminalWidth - 34));

    return (
      <Box flexDirection="column" paddingX={1}>
        <Box>
          <Text bold color={Theme.textPrimary}>
            Conversations
          </Text>
          {loading && (
            <Text color={Theme.stateActive}>
              {" "}
              <Spinner type="dots" />
            </Text>
          )}
          {rows.length > windowSize && (
            <Text color={Theme.textChrome}>
              {"  "}
              {start + 1}–{Math.min(start + windowSize, rows.length)} of{" "}
              {rows.length}
            </Text>
          )}
          {appliedQuery !== "" && (
            <Text color={Theme.accentUser}>
              {"  "}
              {canSearch && organized ? "search" : "filter"}: {appliedQuery}
            </Text>
          )}
        </Box>

        {error && (
          <Box marginTop={1}>
            <Text color={Theme.stateFailed}>{error}</Text>
          </Box>
        )}

        <Box flexDirection="column" marginTop={1}>
          {visible.map((row, i) => {
            const isSelected = start + i === index;
            return (
              <Box key={row.id} flexDirection="column">
                <Box>
                  <Text color={Theme.focus}>{isSelected ? "▶ " : "  "}</Text>
                  <Text color={Theme.stateBlocked}>
                    {row.pinned ? "★ " : "  "}
                  </Text>
                  <Box width={titleWidth}>
                    <Text
                      color={
                        row.archived ? Theme.textChrome : Theme.textPrimary
                      }
                      bold={isSelected}
                    >
                      {truncate(rowTitle(row), titleWidth - 1)}
                    </Text>
                  </Box>
                  <Text color={Theme.textChrome}> {rowMeta(row, now)}</Text>
                  {row.active && <Text color={Theme.stateActive}> ● live</Text>}
                  {row.pendingAttention > 0 && (
                    <Text color={Theme.stateBlocked}>
                      {" "}
                      ⚠ {row.pendingAttention}
                    </Text>
                  )}
                </Box>
                {row.preview && (
                  <Box paddingLeft={4}>
                    <Text color={Theme.textSecondary}>
                      {truncate(row.preview, Math.max(20, terminalWidth - 8))}
                    </Text>
                  </Box>
                )}
              </Box>
            );
          })}

          {rows.length === 0 && !loading && (
            <Text color={Theme.textChrome}>
              {appliedQuery === ""
                ? "No conversations yet — press Tab to start one."
                : `Nothing matches "${appliedQuery}" — Esc clears the ${canSearch && organized ? "search" : "filter"}.`}
            </Text>
          )}
        </Box>

        {mode === "search" && (
          <Box marginTop={1}>
            <Text color={Theme.textChrome}>search: </Text>
            <Text color={Theme.textPrimary}>{buffer}</Text>
            <Text color={Theme.stateActive}>█</Text>
          </Box>
        )}

        {mode === "rename" && (
          <Box marginTop={1}>
            <Text color={Theme.textChrome}>title: </Text>
            <Text color={Theme.textPrimary}>{buffer}</Text>
            <Text color={Theme.stateActive}>█</Text>
          </Box>
        )}

        {mode === "confirm" && confirm && (
          <Box flexDirection="column" marginTop={1}>
            <Text color={Theme.stateBlocked}>{confirm.message}</Text>
            <Text color={Theme.textChrome}>y confirm · n cancel</Text>
          </Box>
        )}

        {statusLine && mode === "list" && (
          <Box marginTop={1}>
            <Text color={Theme.stateBlocked} italic>
              {statusLine}
            </Text>
          </Box>
        )}

        {mode === "list" && (
          <Box flexDirection="column" marginTop={1}>
            <Text color={Theme.textChrome}>
              ↑↓ select · enter open · tab new · /{" "}
              {canSearch && organized ? "search" : "filter"} · d delete
            </Text>
            {organized ? (
              <Text color={Theme.textChrome}>
                p pin · r rename · a archive{canFork ? " · f fork" : ""}
                {canHold ? " · h hold" : ""}
              </Text>
            ) : (
              <Text color={Theme.textChrome}>
                this server predates conversation organizing — pin/rename/fork
                unavailable
              </Text>
            )}
          </Box>
        )}
      </Box>
    );
  },
);

SessionManager.displayName = "SessionManager";
