import React, { useCallback, useEffect, useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "../rpc/client.js";
import { useActions, useAppStore } from "../store/appStore.js";
import {
  buildModelRows,
  initialIndex,
  windowStart,
  type ModelRow,
} from "./model-picker-model.js";
import { RowText, SelectableRow } from "./shared/SelectableRow.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

const WINDOW = 8;

type Props = {
  open: boolean;
  onClose: () => void;
  client: RPCClient | null;
  sessionId?: string;
};

// Model picker dock panel, opened by the /model built-in. Lists the
// provider-neutral catalog (list_models, text modality) and switches the
// session's model via set_session_model — the same stable RPC pair web's
// ModelPicker.tsx consumes. Switching needs a session; before the first
// message the panel says so instead of failing.
export const ModelPickerPanel = themedMemo(
  ({ open, onClose, client, sessionId }: Props) => {
    const actions = useActions();
    const [rows, setRows] = useState<ModelRow[]>([]);
    const [index, setIndex] = useState(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [statusLine, setStatusLine] = useState<string | null>(null);
    const [switching, setSwitching] = useState(false);

    const load = useCallback(async () => {
      if (!client) return;
      setLoading(true);
      setError(null);
      try {
        const result = await client.listModels({
          sessionId,
          modality: "text",
        });
        const built = buildModelRows(
          result,
          useAppStore.getState().sessionModel,
        );
        setRows(built);
        setIndex(initialIndex(built));
        if (result.errors.length > 0) {
          setStatusLine(
            `${result.errors[0]!.provider}: ${result.errors[0]!.message}`,
          );
        }
      } catch (e) {
        setError((e as Error).message ?? String(e));
      } finally {
        setLoading(false);
      }
    }, [client, sessionId]);

    useEffect(() => {
      if (!open) {
        setStatusLine(null);
        setError(null);
        return;
      }
      void load();
      // Deps intentionally omit load: fetch on open only; 'r' refreshes.
    }, [open]);

    const select = useCallback(
      async (row: ModelRow) => {
        if (!client) return;
        if (!sessionId) {
          setStatusLine("no active session yet — send a message first");
          return;
        }
        setSwitching(true);
        setStatusLine(`switching to ${row.label}…`);
        try {
          const result = await client.setSessionModel(sessionId, row.id);
          if (result.ok) {
            actions.setSessionState({
              model: result.model ?? row.id,
              label: result.label ?? row.label,
              provider: result.provider ?? row.provider,
              reasoning_effort: result.reasoning_effort ?? null,
            });
            onClose();
          } else {
            setStatusLine(
              result.reasons?.[0]?.message ?? "model switch rejected",
            );
          }
        } catch (e) {
          setStatusLine((e as Error).message ?? String(e));
        } finally {
          setSwitching(false);
        }
      },
      [client, sessionId, actions, onClose],
    );

    useKeypress(
      (input, key) => {
        // Modified keys are chords for the global layer (quit, panel
        // switching) — never mistake Ctrl+X for the letter x.
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.tab) return false; // tab always cycles screens
        if (key.escape || input === "q") {
          onClose();
          return true;
        }
        if (input === "r") {
          void load();
          return true;
        }
        if (rows.length === 0) return true;
        if (key.upArrow || input === "k") {
          setIndex((prev) => (prev > 0 ? prev - 1 : prev));
          return true;
        }
        if (key.downArrow || input === "j") {
          setIndex((prev) => (prev < rows.length - 1 ? prev + 1 : prev));
          return true;
        }
        if (key.return && !switching) {
          const row = rows[index];
          if (row) void select(row);
          return true;
        }
        return true; // modal — swallow the rest
      },
      { isActive: open, priority: KeypressPriority.High },
    );

    if (!open) return null;

    const start = windowStart(index, rows.length, WINDOW);
    const visible = rows.slice(start, start + WINDOW);

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.stateActive}
      >
        <Box>
          <Text color={Theme.textChrome}>Models</Text>
          {(loading || switching) && (
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

        {error && <Text color={Theme.stateFailed}>{error}</Text>}

        {visible.map((row, i) => {
          const isSelected = start + i === index;
          return (
            <SelectableRow key={row.id} selected={isSelected}>
              <RowText
                color={
                  row.active
                    ? Theme.stateDone
                    : row.available
                      ? Theme.textPrimary
                      : Theme.textChrome
                }
                bold={row.active}
              >
                {row.label}
              </RowText>
              {row.provider && (
                <RowText color={Theme.textChrome}> · {row.provider}</RowText>
              )}
              {row.active && (
                <RowText color={Theme.stateDone}> ✔ active</RowText>
              )}
              {row.isDefault && !row.active && (
                <RowText color={Theme.textChrome}> (default)</RowText>
              )}
              {row.reasoningOptions.length > 0 && (
                <RowText color={Theme.textChrome}> ⚙</RowText>
              )}
              {row.note && (
                <RowText color={Theme.stateBlocked}> ⚠ {row.note}</RowText>
              )}
            </SelectableRow>
          );
        })}

        {rows.length === 0 && !loading && !error && (
          <Text color={Theme.textChrome}>no models configured</Text>
        )}

        {statusLine && (
          <Text color={Theme.stateBlocked} italic>
            {statusLine}
          </Text>
        )}

        <Text color={Theme.textChrome}>
          ↑↓ select · enter switch · r refresh · esc close
        </Text>
      </Box>
    );
  },
);
