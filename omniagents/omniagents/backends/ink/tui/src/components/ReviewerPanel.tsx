import React, { useCallback, useEffect, useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "@omniagents/client/rpc/client";
import { ModelCatalogClient } from "@omniagents/client/rpc/model-catalog";
import {
  APPROVALS_CONFIG_KEY,
  APPROVALS_OPTIONS,
  CHECKS_CONFIG_KEY,
  CHECKS_OPTIONS,
  configFieldValue,
  DEFAULT_APPROVALS_REVIEWER,
  DEFAULT_WORKFLOW_REVIEWER,
  resolveReviewer,
  sourceNote,
  type ReviewerOption,
  type ReviewerSource,
} from "./reviewer-model.js";
import { RowText, SelectableRow } from "./shared/SelectableRow.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

/**
 * The two reviewer switches, `/approvals` and `/checks`.
 *
 * One component because they are structurally identical — two options,
 * the same read (session override + layered config) and the same write —
 * but TWO commands, because they are independent switches over different
 * subjects: who answers a tool approval, and who reviews a plan-step
 * completion. Merging them into one "session controls" panel would blur
 * which transcript record belongs to which switch.
 *
 * The current value is RESOLVED, not assumed: session override, then
 * config, then the documented default, with a note saying which. A
 * switch that renders a guess is a switch that can lie about its own
 * position.
 */

export type ReviewerKind = "approvals" | "checks";

type Props = {
  kind: ReviewerKind;
  open: boolean;
  onClose: () => void;
  client: RPCClient | null;
  sessionId?: string;
};

const TITLES: Record<ReviewerKind, string> = {
  approvals: "Tool approvals",
  checks: "Task checks",
};

/** What each switch governs — the panel doubles as its documentation. */
const SUBTITLES: Record<ReviewerKind, string> = {
  approvals: "who answers a tool or MCP approval",
  checks: "who reviews a plan step against its exit criteria",
};

export const ReviewerPanel = themedMemo<Props>(
  ({ kind, open, onClose, client, sessionId }) => {
    const options = (
      kind === "approvals" ? APPROVALS_OPTIONS : CHECKS_OPTIONS
    ) as readonly ReviewerOption<string>[];
    const configKey =
      kind === "approvals" ? APPROVALS_CONFIG_KEY : CHECKS_CONFIG_KEY;
    const fallback =
      kind === "approvals"
        ? DEFAULT_APPROVALS_REVIEWER
        : DEFAULT_WORKFLOW_REVIEWER;

    const [current, setCurrent] = useState<string>(fallback);
    const [source, setSource] = useState<ReviewerSource>("default");
    const [index, setIndex] = useState(0);
    const [loading, setLoading] = useState(false);
    const [statusLine, setStatusLine] = useState<string | null>(null);
    const [mutating, setMutating] = useState(false);

    const load = useCallback(async () => {
      if (!client) return;
      setLoading(true);
      setStatusLine(null);
      try {
        // The session override and the config default are two different
        // reads; the server's own precedence needs both.
        const [catalog, config] = await Promise.all([
          sessionId
            ? new ModelCatalogClient(client).listModels({
                sessionId,
                modality: "text",
              })
            : Promise.resolve(null),
          client
            .request("get_config", {})
            .catch(() => null as Record<string, unknown> | null),
        ]);
        const override =
          kind === "approvals"
            ? catalog?.session?.approvals_reviewer
            : catalog?.session?.workflow_reviewer;
        const resolved = resolveReviewer(
          override,
          configFieldValue(config, configKey),
          fallback,
          options.map((option) => option.value),
        );
        setCurrent(resolved.value);
        setSource(resolved.source);
        const position = options.findIndex(
          (option) => option.value === resolved.value,
        );
        setIndex(position >= 0 ? position : 0);
      } catch (e) {
        setStatusLine((e as Error).message ?? String(e));
      } finally {
        setLoading(false);
      }
    }, [client, sessionId, kind, configKey, fallback, options]);

    useEffect(() => {
      if (!open) {
        setStatusLine(null);
        return;
      }
      void load();
      // Deps intentionally omit `load`: fetch on open, not every render.
    }, [open]);

    const select = useCallback(
      async (option: ReviewerOption<string>) => {
        if (!client) return;
        if (!sessionId) {
          // Both RPCs are session-scoped; before the first message there
          // is nothing to scope the override to.
          setStatusLine("no active session yet — send a message first");
          return;
        }
        if (option.value === current && source === "session") {
          onClose();
          return;
        }
        setMutating(true);
        setStatusLine(`switching to ${option.label}…`);
        try {
          const result =
            kind === "approvals"
              ? await client.setSessionApprovals(
                  sessionId,
                  option.value as "user" | "auto",
                )
              : await client.setSessionWorkflow(
                  sessionId,
                  option.value as "off" | "guardian",
                );
          if (result?.ok === false) {
            setStatusLine(
              typeof result.reason === "string"
                ? result.reason
                : "the server rejected that reviewer",
            );
            return;
          }
          setCurrent(option.value);
          setSource("session");
          onClose();
        } catch (e) {
          setStatusLine((e as Error).message ?? String(e));
        } finally {
          setMutating(false);
        }
      },
      [client, sessionId, kind, current, source, onClose],
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
        if (key.upArrow || input === "k") {
          setIndex((prev) => (prev > 0 ? prev - 1 : prev));
          return true;
        }
        if (key.downArrow || input === "j") {
          setIndex((prev) => (prev < options.length - 1 ? prev + 1 : prev));
          return true;
        }
        if (key.return && !mutating) {
          const option = options[index];
          if (option) void select(option);
          return true;
        }
        return true; // modal — swallow the rest
      },
      { isActive: open, priority: KeypressPriority.High },
    );

    if (!open) return null;

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.stateActive}
      >
        <Box>
          <Text color={Theme.textChrome}>{TITLES[kind]}</Text>
          {(loading || mutating) && (
            <Text color={Theme.stateActive}>
              {" "}
              <Spinner type="dots" />
            </Text>
          )}
          <Text color={Theme.textChrome}> · {SUBTITLES[kind]}</Text>
        </Box>

        {options.map((option, i) => {
          const isSelected = i === index;
          const isActive = option.value === current;
          return (
            <SelectableRow key={option.value} selected={isSelected}>
              <RowText
                color={isActive ? Theme.stateDone : Theme.textPrimary}
                bold={isActive}
              >
                {option.label}
              </RowText>
              {isActive && (
                <RowText color={Theme.stateDone}>
                  {" "}
                  ✔ {sourceNote(source)}
                </RowText>
              )}
              <RowText color={Theme.textChrome}> · {option.hint}</RowText>
            </SelectableRow>
          );
        })}

        {statusLine && (
          <Text color={Theme.stateBlocked} italic>
            {statusLine}
          </Text>
        )}

        <Text color={Theme.textChrome}>
          ↑↓ select · enter apply · esc close
        </Text>
      </Box>
    );
  },
);

ReviewerPanel.displayName = "ReviewerPanel";
