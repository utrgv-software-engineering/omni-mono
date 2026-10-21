/**
 * Structured user-elicitation dialog (protocol.md § "User Elicitation
 * Flow"). A TUI renders what it can and declines what it can't:
 *
 * - ``confirm``  → yes / no / decline selection list
 * - ``select``   → the offered options as a selection list (a
 *                  multi-select schema degrades to picking one — still a
 *                  valid subset for the server contract)
 * - ``url``      → the URL is printed to complete out-of-band, with
 *                  done / decline
 * - ``form`` and unknown kinds → a typed decline
 *   (``action: "decline"``, reason ``unsupported_by_client``) — never a
 *   crash, never a silent drop. Another connected client (web UI) may
 *   still answer; the prompt dismisses everywhere on resolution.
 *
 * ``question`` prompts are NOT rendered here — they reuse the escalation
 * banner idiom: the next message the user submits becomes the answer
 * (see ElicitationBanner and the App.tsx submit interceptor).
 */

import React from "react";
import { Box, Text } from "ink";
import { Theme } from "../theme.js";
import type { ElicitationInfo } from "../store/appStore.js";
import { DialogShell } from "./shared/DialogShell.js";
import {
  BaseSelectionList,
  type SelectionOption,
} from "./shared/BaseSelectionList.js";
import { themedMemo } from "../theme/themed-memo.js";

export type ElicitationAnswer = {
  action: "accept" | "decline" | "cancel";
  value?: Record<string, unknown>;
  reason?: string;
};

export interface ElicitationDialogProps {
  elicitation: ElicitationInfo;
  onRespond: (answer: ElicitationAnswer) => void;
}

export type NormalizedOption = {
  value: string | number | boolean;
  label: string;
  description?: string;
};

/** Normalize the open-record options array; null = not renderable. */
export function normalizeOptions(
  options: unknown[] | undefined,
): NormalizedOption[] | null {
  if (!options || options.length === 0) return null;
  const entries: NormalizedOption[] = [];
  for (const raw of options) {
    if (
      typeof raw === "string" ||
      typeof raw === "number" ||
      typeof raw === "boolean"
    ) {
      entries.push({ value: raw, label: String(raw) });
      continue;
    }
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      const record = raw as Record<string, unknown>;
      const value = record.value;
      if (
        typeof value === "string" ||
        typeof value === "number" ||
        typeof value === "boolean"
      ) {
        entries.push({
          value,
          label:
            typeof record.label === "string" && record.label
              ? record.label
              : String(value),
          description:
            typeof record.description === "string"
              ? record.description
              : undefined,
        });
        continue;
      }
    }
    return null;
  }
  return entries;
}

/** What this TUI can do with a prompt. ``question`` is handled by the
 *  banner path, so it never reaches the dialog. */
export function dialogSupport(elicitation: ElicitationInfo): {
  supported: boolean;
  reason?: string;
} {
  if (elicitation.kind === "confirm") return { supported: true };
  if (elicitation.kind === "select") {
    return normalizeOptions(elicitation.options)
      ? { supported: true }
      : {
          supported: false,
          reason: "select prompt carries no renderable options",
        };
  }
  if (elicitation.kind === "url") {
    return elicitation.url
      ? { supported: true }
      : { supported: false, reason: "url prompt carries no url" };
  }
  if (elicitation.kind === "form") {
    return { supported: false, reason: "forms are not renderable in the TUI" };
  }
  return {
    supported: false,
    reason: `unsupported elicitation kind "${elicitation.kind}"`,
  };
}

const CONFIRM_OPTIONS: SelectionOption<"yes" | "no" | "decline">[] = [
  { title: "1. Yes", description: "Confirm", value: "yes" },
  { title: "2. No", description: "Answer no", value: "no" },
  { title: "3. Decline", description: "Refuse to answer", value: "decline" },
];

const URL_OPTIONS: SelectionOption<"done" | "decline">[] = [
  {
    title: "1. I've done this",
    description: "Mark the action complete",
    value: "done",
  },
  { title: "2. Decline", description: "Refuse the action", value: "decline" },
];

const DECLINE_OPTIONS: SelectionOption<"decline">[] = [
  {
    title: "1. Decline",
    description: "Send a typed decline (another client may still answer)",
    value: "decline",
  },
];

export const ElicitationDialog = themedMemo<ElicitationDialogProps>(
  ({ elicitation, onRespond }) => {
    const support = dialogSupport(elicitation);
    const title = elicitation.title || "Input requested";

    const body = (
      <Box flexDirection="column">
        <Text color={Theme.textChrome}>{elicitation.message}</Text>
        {elicitation.expires_at ? (
          <Text color={Theme.textSecondary}>
            Expires {elicitation.expires_at}
          </Text>
        ) : null}
      </Box>
    );

    if (!support.supported) {
      return (
        <DialogShell
          title={title}
          footer="This terminal can't render this request — decline it or answer from the web UI."
        >
          {body}
          <Text color={Theme.stateBlocked}>({support.reason})</Text>
          <BaseSelectionList
            options={DECLINE_OPTIONS}
            onSelect={() =>
              onRespond({ action: "decline", reason: "unsupported_by_client" })
            }
          />
        </DialogShell>
      );
    }

    if (elicitation.kind === "confirm") {
      return (
        <DialogShell title={title} footer="Use ↑↓ to navigate, Enter to select">
          {body}
          <BaseSelectionList
            options={CONFIRM_OPTIONS}
            onSelect={(choice) =>
              choice === "decline"
                ? onRespond({ action: "decline" })
                : onRespond({
                    action: "accept",
                    value: { confirmed: choice === "yes" },
                  })
            }
          />
        </DialogShell>
      );
    }

    if (elicitation.kind === "url") {
      return (
        <DialogShell
          title={title}
          footer="Open the link in your browser, then select an option"
        >
          {body}
          <Text color={Theme.focus}>{elicitation.url}</Text>
          <BaseSelectionList
            options={URL_OPTIONS}
            onSelect={(choice) =>
              choice === "done"
                ? onRespond({ action: "accept", value: { completed: true } })
                : onRespond({ action: "decline" })
            }
          />
        </DialogShell>
      );
    }

    // select
    const entries = normalizeOptions(elicitation.options) || [];
    const options: SelectionOption<string>[] = entries.map((entry, index) => ({
      title: `${index + 1}. ${entry.label}`,
      description: entry.description || String(entry.value),
      value: JSON.stringify(entry.value),
    }));
    options.push({
      title: `${entries.length + 1}. Decline`,
      description: "Refuse to choose",
      value: "decline",
    });
    return (
      <DialogShell title={title} footer="Use ↑↓ to navigate, Enter to select">
        {body}
        <BaseSelectionList
          options={options}
          onSelect={(choice) => {
            if (choice === "decline") {
              onRespond({ action: "decline" });
              return;
            }
            const entry = entries.find(
              (entry) => JSON.stringify(entry.value) === choice,
            );
            if (entry) {
              onRespond({
                action: "accept",
                value: { selected: [entry.value] },
              });
            }
          }}
        />
      </DialogShell>
    );
  },
);
