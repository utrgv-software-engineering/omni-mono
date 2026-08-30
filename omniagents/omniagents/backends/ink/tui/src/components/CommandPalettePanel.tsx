import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "../rpc/client.js";
import {
  buildCommandRows,
  completionFor,
  filterCommands,
  type CommandRow,
} from "./command-palette-model.js";
import { windowStart } from "./model-picker-model.js";
import { RowText, SelectableRow } from "./shared/SelectableRow.js";
import { Theme } from "../theme.js";

const WINDOW = 8;

type Props = {
  open: boolean;
  onClose: () => void;
  client: RPCClient | null;
  /**
   * Hand the chosen command to the composer. The panel never submits on
   * the user's behalf: a slash command can take arguments, and running
   * one the moment it is highlighted would fire `/purge` before the user
   * finished reading it.
   */
  onComplete: (text: string) => void;
};

/**
 * The slash-command palette (ctrl+k, or `/help`).
 *
 * `handleSlashCommand` has always called `list_server_functions` — but
 * only to check the name the user already guessed, which makes it a
 * validator, not a directory. This is the directory: builtins and server
 * functions in one filterable list, each with the description the server
 * declares, completing into the composer so the user can add arguments
 * before committing.
 *
 * Same disclosure philosophy as the ctrl+a which-key menu: the menu IS
 * the documentation, and the direct `/name` spelling is the accelerator
 * you graduate into.
 */
export const CommandPalettePanel = React.memo<Props>(
  ({ open, onClose, client, onComplete }) => {
    const [all, setAll] = useState<CommandRow[]>([]);
    const [query, setQuery] = useState("");
    const [index, setIndex] = useState(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
      if (!client) return;
      setLoading(true);
      setError(null);
      try {
        setAll(buildCommandRows(await client.listServerFunctions()));
      } catch (e) {
        // The builtins are client-side and still worth listing when the
        // server half is unreachable — a palette that renders nothing
        // because one call failed teaches the user it is broken.
        setAll(buildCommandRows([]));
        setError((e as Error).message ?? String(e));
      } finally {
        setLoading(false);
      }
    }, [client]);

    useEffect(() => {
      if (!open) {
        setQuery("");
        setIndex(0);
        setError(null);
        return;
      }
      void load();
      // Deps intentionally omit `load`: fetch on open, not on every render.
    }, [open]);

    const rows = useMemo(() => filterCommands(all, query), [all, query]);

    // Filtering shortens the list under a cursor that was addressing the
    // longer one; clamp on the way in rather than rendering a gap.
    const cursor = rows.length === 0 ? 0 : Math.min(index, rows.length - 1);

    useKeypress(
      (input, key) => {
        // Modified keys are chords for the global layer (quit, panel
        // switching) — never mistake Ctrl+X for the letter x.
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.tab) return false; // tab always cycles screens
        if (key.escape) {
          onClose();
          return true;
        }
        // Functional updates, and clamped against `rows` on the way out
        // as well as on the way in: two arrows delivered in one tick
        // would both read the same stale `cursor` otherwise.
        if (key.upArrow) {
          setIndex((prev) => Math.max(0, Math.min(prev, rows.length - 1) - 1));
          return true;
        }
        if (key.downArrow) {
          setIndex((prev) => Math.min(prev + 1, rows.length - 1));
          return true;
        }
        if (key.return) {
          const row = rows[cursor];
          if (row) {
            onComplete(completionFor(row));
            onClose();
          }
          return true;
        }
        if (key.backspace || key.delete) {
          setQuery((prev) => prev.slice(0, -1));
          setIndex(0);
          return true;
        }
        if (input) {
          setQuery((prev) => prev + input);
          setIndex(0);
          return true;
        }
        return true; // modal — swallow the rest
      },
      { isActive: open, priority: KeypressPriority.High },
    );

    if (!open) return null;

    const start = windowStart(cursor, rows.length, WINDOW);
    const visible = rows.slice(start, start + WINDOW);

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.stateActive}
      >
        <Box>
          <Text color={Theme.textChrome}>Commands</Text>
          {loading && (
            <Text color={Theme.stateActive}>
              {" "}
              <Spinner type="dots" />
            </Text>
          )}
          {rows.length > WINDOW && (
            <Text color={Theme.textChrome}>
              {"  "}
              {start + 1}–{Math.min(start + WINDOW, rows.length)} of{" "}
              {rows.length}
            </Text>
          )}
        </Box>

        <Box>
          <Text color={Theme.textChrome}>/</Text>
          <Text color={Theme.textPrimary}>{query}</Text>
          <Text color={Theme.stateActive}>█</Text>
        </Box>

        {error && (
          <Text color={Theme.stateFailed}>
            server functions unavailable: {error}
          </Text>
        )}

        {visible.map((row, i) => {
          const isSelected = start + i === cursor;
          return (
            <SelectableRow
              key={`${row.source}:${row.name}`}
              selected={isSelected}
            >
              <RowText color={Theme.textPrimary}>/{row.name}</RowText>
              {row.takesArgs && (
                <RowText color={Theme.textChrome}> &lt;args&gt;</RowText>
              )}
              {row.source === "builtin" && (
                <RowText color={Theme.accentUser}> builtin</RowText>
              )}
              {row.description && (
                <RowText color={Theme.textChrome}> · {row.description}</RowText>
              )}
            </SelectableRow>
          );
        })}

        {rows.length === 0 && !loading && (
          <Text color={Theme.textChrome}>
            {query === ""
              ? "no commands available"
              : `nothing matches "${query}"`}
          </Text>
        )}

        <Text color={Theme.textChrome}>
          type to filter · ↑↓ select · enter insert · esc close
        </Text>
      </Box>
    );
  },
);

CommandPalettePanel.displayName = "CommandPalettePanel";
