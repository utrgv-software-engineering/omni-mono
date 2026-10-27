import { WorkspaceView } from "./WorkspaceView.js";
import { useImmediateState } from "../hooks/useImmediateState.js";
import { useKeyedSelection } from "../hooks/useKeyedSelection.js";
import { useRequestScope } from "../hooks/useRequestScope.js";
import { isReplyLost } from "@omniagents/client/lifecycle";
import { ConversationOrganizationClient } from "@omniagents/client/rpc/conversation-organization";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Box, Text } from "ink";
import { useTerminalSize } from "../hooks/useTerminalSize.js";
import Spinner from "ink-spinner";
import { useKeypress, type Key } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "@omniagents/client/rpc/client";
import {
  filterRows,
  rowMeta,
  rowsFromSearch,
  rowsFromThreads,
  rowTitle,
  sortRows,
  truncate,
  type SessionRow,
} from "./session-manager-model.js";
import { windowStart } from "./model-picker-model.js";
import { RowText, SelectableRow } from "./shared/SelectableRow.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

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
  /**
   * Go back to the conversation the user came from (Esc in the list).
   * Absent at launch, where there is nothing to go back to.
   */
  onExit?: () => void;
};

export const SessionManager = themedMemo<Props>((props) => (
  <WorkspaceView client={props.client} active>
    <SessionManagerContent {...props} />
  </WorkspaceView>
));
const SessionManagerContent = themedMemo<Props>(
  ({ client, onOpen, onNew, onEmpty, onExit }) => {
    const requests = useRequestScope([client]);
    const operationPending = useRef(false);
    const leaving = useRef(false);
    const renameTarget = useRef<string | null>(null);
    const { rows: terminalHeight, columns: terminalWidth } = useTerminalSize();
    const windowSize = Math.max(4, terminalHeight - CHROME_ROWS);

    const [rows, setRows] = useState<SessionRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [statusLine, setStatusLine] = useState<string | null>(null);
    const [mode, setMode, modeRef] = useImmediateState<Mode>("list");
    const [buffer, setBuffer, bufferRef] = useImmediateState("");
    const [confirm, setConfirm, confirmRef] =
      useImmediateState<PendingConfirm | null>(null);
    /** The query the CURRENT rows were drawn under, "" for the full list. */
    const [appliedQuery, setAppliedQuery] = useState("");
    const [now, setNow] = useState(() => Date.now());

    // `list_threads` and its siblings are experimental: the server echoes
    // the grant at initialize, so feature-detection is a field read rather
    // than a -32013 round trip.
    //
    // The grants are a field read, only trustworthy once the handshake has
    // landed; App routes here only after it has.
    const granted = (operation: string) =>
      client?.supportsExperimentalOperation(operation) ?? false;
    const canSearch = granted("search_threads");
    const canUpdate = granted("update_thread");
    const canFork = granted("fork_session");
    const canHold = granted("set_session_hold");
    const conversations = useMemo(
      () => (client ? new ConversationOrganizationClient(client) : null),
      [client],
    );
    useEffect(() => () => conversations?.dispose(), [conversations]);

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
        if (!conversations) {
          // No transport: settle into a definite "nothing loaded" rather
          // than an eternal spinner the empty-check would wait behind.
          setLoading(false);
          return;
        }
        const current = requests.begin("load");
        appliedQueryRef.current = query;
        setLoading(true);
        setError(null);
        try {
          let next: SessionRow[];
          if (query !== "" && canSearch) {
            // Rank order is the server's; sorting it would discard the ranking.
            next = rowsFromSearch(
              await conversations.searchThreads(query, { limit: 100 }),
            );
          } else {
            next = sortRows(
              rowsFromThreads(await conversations.listThreads({ limit: 100 })),
            );
            if (query !== "") next = filterRows(next, query);
          }
          if (!current()) return;
          setRows(next);
          setAppliedQuery(query);
        } catch (e) {
          if (!current()) return;
          setError((e as Error).message ?? String(e));
        } finally {
          if (current()) setLoading(false);
        }
      },
      [conversations, canSearch],
    );

    // Initial load. `load` is stable for a given client/grant set.
    useEffect(() => {
      setRows([]);
      setMode("list");
      setBuffer("");
      leaving.current = false;
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
    useEffect(() => {
      if (!client) return;
      const refetch = () => {
        void load(appliedQueryRef.current);
      };
      const offUpdated = client.on("thread_updated", refetch);
      const offConnected = client.onConnectionState((connection) => {
        if (connection === "connected") refetch();
      });
      return () => {
        offUpdated();
        offConnected();
      };
    }, [client, load]);

    const selection = useKeyedSelection(rows, (row) => row.id);
    const index = selection.index;

    /**
     * Run a mutation, then refetch. Failures land on the status line
     * rather than replacing the screen: the list is still usable when a
     * single rename was rejected.
     */
    const mutate = useCallback(
      async (label: string, run: () => Promise<unknown>) => {
        if (operationPending.current || leaving.current) return;
        operationPending.current = true;
        const current = requests.begin("mutation");
        try {
          setStatusLine(`${label}…`);
          await run();
          if (!current()) return;
          await load(appliedQueryRef.current);
          if (current()) setStatusLine(label);
        } catch (e) {
          if (!current()) return;
          if (isReplyLost(e)) {
            // The reply was lost, not necessarily the mutation. Re-read
            // server truth: now if the socket is already back, otherwise
            // the rpc_ready subscription refetches on reconnect.
            setStatusLine(null);
            if (client?.initializeResult) void load(appliedQueryRef.current);
            return;
          }
          setStatusLine(
            `${label} failed: ${(e as Error).message ?? String(e)}`,
          );
        } finally {
          operationPending.current = false;
        }
      },
      [client, load],
    );

    const requireOrganize = useCallback(
      (row: SessionRow | undefined): row is SessionRow => {
        if (!row) return false;
        if (!canUpdate) {
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

    const handleList = (input: string, key: Key): boolean => {
      if (modeRef.current !== "list") return false;
      if (leaving.current) return true;
      if (key.paste) return true;
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;

      const selected = selection.current();
      if (key.tab) {
        leaving.current = true;
        requests.invalidate();
        onNew();
        return true;
      }
      if (key.upArrow || input === "k") {
        selection.move(-1);
        return true;
      }
      if (key.downArrow || input === "j") {
        selection.move(1);
        return true;
      }
      if (key.return) {
        if (selected) {
          leaving.current = true;
          requests.invalidate();
          onOpen(selected.id);
        }
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
      if (key.escape && onExit) {
        leaving.current = true;
        requests.invalidate();
        onExit();
        return true;
      }
      if (!selected || operationPending.current) return true;

      if (input === "p") {
        if (!requireOrganize(selected)) return true;
        void mutate(selected.pinned ? "unpinned" : "pinned", () =>
          conversations!.updateThread(selected.id, {
            pinned: !selected.pinned,
          }),
        );
        return true;
      }
      if (input === "r") {
        if (!requireOrganize(selected)) return true;
        renameTarget.current = selected.id;
        setMode("rename");
        setBuffer(selected.title ?? "");
        return true;
      }
      if (input === "a") {
        if (!requireOrganize(selected)) return true;
        const next = selected.archived ? "active" : "archived";
        void mutate(next === "archived" ? "archived" : "unarchived", () =>
          conversations!.updateThread(selected.id, { status: next }),
        );
        return true;
      }
      if (input === "f") {
        if (!canFork) {
          setStatusLine("this server does not support forking");
          return true;
        }
        operationPending.current = true;
        const current = requests.begin("fork");
        void (async () => {
          try {
            setStatusLine("forking…");
            const result = await conversations!.forkSession(selected.id);
            if (!current() || leaving.current) return;
            const forked = result?.new_session_id;
            if (typeof forked === "string" && forked !== "") {
              // Opening the child is the whole point of the gesture —
              // dropping the user back on the list would make them hunt
              // for a conversation they just created.
              leaving.current = true;
              onOpen(forked);
              return;
            }
            setStatusLine("fork returned no session id");
          } catch (e) {
            if (!current()) return;
            if (isReplyLost(e)) {
              // A forked child, if it exists, shows up in the list: refetch
              // now if connected, else on rpc_ready.
              setStatusLine(null);
              if (client?.initializeResult) void load(appliedQueryRef.current);
              return;
            }
            setStatusLine(`fork failed: ${(e as Error).message ?? String(e)}`);
          } finally {
            operationPending.current = false;
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
          client!.request("set_session_hold", {
            session_id: selected.id,
            hold: true,
          }),
        );
        return true;
      }
      if (input === "d") {
        setConfirm({
          label: "delete",
          message: `Delete "${rowTitle(selected)}" and its history? This cannot be undone.`,
          run: async () => {
            await client!.request("delete_session", {
              session_id: selected.id,
            });
          },
        });
        setMode("confirm");
        return true;
      }
      return true; // full-screen: swallow the rest
    };

    // ----- search-mode keys ----------------------------------------------

    const handleSearch = (input: string, key: Key): boolean => {
      if (modeRef.current !== "search") return false;
      if (leaving.current) return true;
      if (key.paste) {
        setBuffer((prev) => prev + input);
        return true;
      }
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.escape) {
        setMode("list");
        setBuffer("");
        return true;
      }
      if (key.return) {
        setMode("list");
        void load(bufferRef.current.trim());
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
    };

    // ----- rename-mode keys ----------------------------------------------

    const handleRename = (input: string, key: Key): boolean => {
      if (modeRef.current !== "rename") return false;
      if (leaving.current) return true;
      if (key.paste) {
        setBuffer((prev) => prev + input);
        return true;
      }
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.escape) {
        setMode("list");
        setBuffer("");
        return true;
      }
      if (key.return) {
        const title = bufferRef.current.trim();
        setMode("list");
        setBuffer("");
        const id = renameTarget.current;
        if (!id) return true;
        // An emptied title is a real intent (clear it, falling back to
        // the id), which the protocol spells as an explicit null —
        // omitting the key would mean "leave it alone" instead.
        void mutate(title === "" ? "title cleared" : "renamed", () =>
          conversations!.updateThread(id, {
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
    };

    // ----- confirm-mode keys ---------------------------------------------

    const handleConfirm = (input: string, key: Key): boolean => {
      if (modeRef.current !== "confirm") return false;
      if (leaving.current) return true;
      if (key.paste) return true;
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (input === "y" && confirm && confirmRef.current === confirm) {
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
    };

    useKeypress(
      (input, key) => {
        const dispatch = (input: string, key: Key) =>
          handleConfirm(input, key) ||
          handleRename(input, key) ||
          handleSearch(input, key) ||
          handleList(input, key);
        if (!key.paste && !key.ctrl && !key.meta && input.length > 1) {
          // Coalesced command keys are separate gestures; text fields receive the remaining text together.
          const chars = [...input];
          for (let i = 0; i < chars.length; i++) {
            if (!(
              modeRef.current === "list" || modeRef.current === "confirm"
            )) {
              dispatch(chars.slice(i).join(""), key);
              break;
            }
            dispatch(chars[i], { ...key, shift: /^[A-Z]$/.test(chars[i]) });
          }
          return true;
        }
        return dispatch(input, key);
      },
      { isActive: true, priority: KeypressPriority.High },
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
              {canSearch ? "search" : "filter"}: {appliedQuery}
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
                <SelectableRow selected={isSelected}>
                  <RowText color={Theme.stateBlocked}>
                    {row.pinned ? "★ " : "  "}
                  </RowText>
                  <Box width={titleWidth}>
                    <RowText
                      color={
                        row.archived ? Theme.textChrome : Theme.textPrimary
                      }
                    >
                      {truncate(rowTitle(row), titleWidth - 1)}
                    </RowText>
                  </Box>
                  <RowText color={Theme.textChrome}>
                    {" "}
                    {rowMeta(row, now)}
                  </RowText>
                  {row.active && (
                    <RowText color={Theme.stateActive}> ● live</RowText>
                  )}
                  {row.pendingAttention > 0 && (
                    <RowText color={Theme.stateBlocked}>
                      {" "}
                      ⚠ {row.pendingAttention}
                    </RowText>
                  )}
                </SelectableRow>
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
                : `Nothing matches "${appliedQuery}" — Esc clears the ${canSearch ? "search" : "filter"}.`}
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
              {canSearch ? "search" : "filter"} · d delete
              {onExit ? " · esc back" : ""}
            </Text>
            <Text color={Theme.textChrome}>
              p pin · r rename · a archive{canFork ? " · f fork" : ""}
              {canHold ? " · h hold" : ""}
            </Text>
          </Box>
        )}
      </Box>
    );
  },
);

SessionManager.displayName = "SessionManager";
