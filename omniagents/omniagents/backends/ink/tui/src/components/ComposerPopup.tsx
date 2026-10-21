import React, { useEffect, useMemo, useRef, useState } from "react";
import { Box, Text } from "ink";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { FsListEntry, RPCClient } from "../rpc/client.js";
import {
  buildCommandRows,
  completionFor,
  filterCommands,
  type CommandRow,
} from "./command-palette-model.js";
import {
  buildMentionRows,
  filterMentions,
  mentionCompletion,
  type MentionRow,
} from "./mention-model.js";
import type { ComposerCompletion, ComposerToken } from "./composer-token.js";
import { enterCompletion } from "../rpc/slash.js";
import { windowStart } from "./model-picker-model.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

const WINDOW = 8;
const MENTION_INDEX_TTL_MS = 30_000;

type Props = {
  /** The token under the cursor, or null when there is nothing to complete. */
  token: ComposerToken | null;
  client: RPCClient | null;
  onComplete: (completion: ComposerCompletion) => void;
  /** The user pressed Esc: hide until the token changes. */
  onDismiss: () => void;
};

/**
 * The inline popup under the composer for `/commands` and `@files`.
 *
 * Non-modal on purpose: it owns only the keys that mean something to a
 * list (arrows, tab, enter, esc) and lets every other key fall through
 * to the composer, so typing keeps filtering. Tab completes without
 * sending; Enter completes and, for a command that takes no arguments,
 * sends. Tab is otherwise the screen switcher, which is why the popup
 * consumes it only while it is open.
 */
export const ComposerPopup = themedMemo<Props>(
  ({ token, client, onComplete, onDismiss }) => {
    const [commands, setCommands] = useState<CommandRow[] | null>(null);
    const [files, setFiles] = useState<MentionRow[] | null>(null);
    const [truncated, setTruncated] = useState(false);
    const [index, setIndex] = useState(0);
    const filesLoadedAt = useRef(0);
    const loading = useRef<Promise<void> | null>(null);

    const kind = token?.kind ?? null;
    const query = token?.query ?? "";

    // Commands: fetched once per client; the builtins render meanwhile.
    useEffect(() => {
      if (kind !== "slash" || commands !== null || !client) return;
      setCommands(buildCommandRows([]));
      void client
        .listSlashCommands()
        .then((rows) => setCommands(buildCommandRows(rows)))
        .catch(() => {});
    }, [kind, commands, client]);
    useEffect(() => setCommands(null), [client]);

    // Files: one recursive listing, cached briefly. Typing never waits on
    // the network; the list fills in when it lands.
    useEffect(() => {
      if (kind !== "mention" || !client) return;
      const environmentId = client.defaultEnvironmentId?.() ?? null;
      const fresh = Date.now() - filesLoadedAt.current < MENTION_INDEX_TTL_MS;
      if (
        !environmentId ||
        !client.isGranted("fs_list") ||
        fresh ||
        loading.current
      )
        return;
      loading.current = client
        .fsList(environmentId, ".", true)
        .then((result) => {
          filesLoadedAt.current = Date.now();
          setFiles(buildMentionRows(result.entries as FsListEntry[]));
          setTruncated(Boolean(result.truncated));
        })
        .catch(() => setFiles([]))
        .finally(() => {
          loading.current = null;
        });
    }, [kind, client]);

    const rows = useMemo(() => {
      if (kind === "slash") return filterCommands(commands ?? [], query);
      if (kind === "mention") return filterMentions(files ?? [], query);
      return [];
    }, [kind, commands, files, query]);

    useEffect(() => setIndex(0), [kind, query]);
    const cursor = rows.length === 0 ? 0 : Math.min(index, rows.length - 1);

    const complete = (row: CommandRow | MentionRow, viaEnter: boolean) => {
      if (!token) return;
      if (kind === "slash") {
        const command = row as CommandRow;
        const completion = viaEnter
          ? enterCompletion(command, query)
          : { text: completionFor(command), submit: false };
        onComplete({ token, ...completion });
      } else {
        onComplete({
          token,
          text: mentionCompletion(row as MentionRow),
          submit: false,
        });
      }
    };

    useKeypress(
      (_input, key) => {
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.escape) {
          onDismiss();
          return true;
        }
        // With nothing listed, arrows are the composer's (cursor, history).
        if (key.upArrow && rows.length > 0) {
          setIndex((prev) => Math.max(0, Math.min(prev, rows.length - 1) - 1));
          return true;
        }
        if (key.downArrow && rows.length > 0) {
          setIndex((prev) => Math.min(prev + 1, rows.length - 1));
          return true;
        }
        if (key.tab || key.return) {
          const row = rows[cursor];
          if (row) {
            complete(row, key.return);
            return true;
          }
          // Nothing to complete: Enter sends as typed, Tab cycles screens.
          return false;
        }
        return false;
      },
      { isActive: token !== null, priority: KeypressPriority.High },
    );

    if (!token) return null;

    const start = windowStart(cursor, rows.length, WINDOW);
    const visible = rows.slice(start, start + WINDOW);
    const empty =
      kind === "mention" && files === null
        ? "Indexing files…"
        : rows.length === 0
          ? kind === "slash"
            ? "No matching command"
            : "No matching file"
          : null;

    return (
      <Box flexDirection="column" paddingX={2}>
        {empty ? (
          <Text color={Theme.textChrome}>{empty}</Text>
        ) : (
          visible.map((row, offset) => {
            const active = start + offset === cursor;
            const label =
              kind === "slash"
                ? `/${(row as CommandRow).name}${(row as CommandRow).usage ? ` ${(row as CommandRow).usage}` : ""}`
                : (row as MentionRow).type === "directory"
                  ? `${(row as MentionRow).path}/`
                  : (row as MentionRow).path;
            const detail =
              kind === "slash" ? ((row as CommandRow).description ?? "") : "";
            // One row per entry, always: a long description is cut, not
            // wrapped, so the popup's height is its row count.
            return (
              <Box key={label} flexShrink={0}>
                <Box flexShrink={0}>
                  <Text
                    color={active ? Theme.accentUser : Theme.textPrimary}
                    bold={active}
                    wrap="truncate-end"
                  >
                    {active ? "▸ " : "  "}
                    {label}
                  </Text>
                </Box>
                {detail ? (
                  <Box flexShrink={1} minWidth={0} paddingLeft={1}>
                    <Text color={Theme.textChrome} wrap="truncate-end">
                      {detail.replace(/\s+/g, " ")}
                    </Text>
                  </Box>
                ) : null}
              </Box>
            );
          })
        )}
        {truncated && kind === "mention" && (
          <Text color={Theme.textChrome}>
            {" "}
            (index truncated; type more of the path)
          </Text>
        )}
      </Box>
    );
  },
);
