import React, { useEffect, useRef, useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import type { RPCClient } from "../rpc/client.js";
import { windowStart } from "./model-picker-model.js";
import { RowText, SelectableRow } from "./shared/SelectableRow.js";
import {
  Clip,
  Fixed,
  oneLine,
  useDrawerBudget,
} from "./shared/drawer-layout.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

export type SkillRow = {
  name: string;
  description: string;
  path: string;
  error?: string;
};

type Props = {
  focused: boolean;
  onUnfocus: () => void;
  client: RPCClient | null;
  sessionId?: string;
};

const MAX_WINDOW = 12;

/**
 * The SKILL.md as a person reads it: the YAML frontmatter is what the
 * header and the list already show, so it is dropped.
 */
export function skillBody(text: string): string {
  const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(text);
  return (match ? text.slice(match[0].length) : text).replace(/^\n+/, "");
}

/** Width of the name column: the longest name, capped so a silly one cannot push every description off-screen. */
export function nameColumnWidth(rows: readonly SkillRow[]): number {
  const longest = rows.reduce((max, r) => Math.max(max, r.name.length), 0);
  return Math.min(Math.max(longest, 8), 28);
}

/** Filter by what the user typed: name prefix first, then description. */
export function filterSkills(
  rows: readonly SkillRow[],
  query: string,
): SkillRow[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...rows];
  const prefix = rows.filter((r) => r.name.toLowerCase().startsWith(needle));
  const rest = rows.filter(
    (r) =>
      !prefix.includes(r) &&
      (r.name.toLowerCase().includes(needle) ||
        r.description.toLowerCase().includes(needle)),
  );
  return [...prefix, ...rest];
}

/**
 * The skills drawer: what `/skills` opens. A navigable list with a
 * type-to-filter, and Enter reads the skill's SKILL.md in place. Codex
 * does the same with a menu and a list view; a dozen skills fit inline,
 * fifty do not.
 */
export const SkillsPanel = themedMemo(
  ({ focused, onUnfocus, client, sessionId }: Props) => {
    const [all, setAll] = useState<SkillRow[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [query, setQuery] = useState("");
    const [index, setIndex] = useState(0);
    const [body, setBody] = useState<{
      name: string;
      text: string;
      scroll: number;
    } | null>(null);
    const loading = useRef(false);
    const BODY_LINES = useDrawerBudget();
    const WINDOW = Math.min(MAX_WINDOW, BODY_LINES);

    useEffect(() => {
      if (!focused) {
        setQuery("");
        setIndex(0);
        setBody(null);
        return;
      }
      if (!client || loading.current) return;
      loading.current = true;
      setError(null);
      client
        .serverCall("skills.list", {}, sessionId)
        .then((res) => {
          const rows = ((res as { skills?: unknown }).skills ??
            []) as SkillRow[];
          setAll(rows.map((r) => ({ ...r, description: r.description ?? "" })));
        })
        .catch((e) => setError((e as Error).message ?? String(e)))
        .finally(() => {
          loading.current = false;
        });
    }, [focused, client, sessionId]);

    const rows = filterSkills(all ?? [], query);
    const cursor = rows.length === 0 ? 0 : Math.min(index, rows.length - 1);

    const read = (row: SkillRow) => {
      if (!client) return;
      setBody({ name: row.name, text: "…", scroll: 0 });
      client
        .serverCall("skills.read", { name: row.name }, sessionId)
        .then((res) => {
          const r = res as { ok?: boolean; text?: string; error?: string };
          setBody({
            name: row.name,
            text:
              r.ok && r.text
                ? skillBody(r.text)
                : (r.error ?? "Could not read skill"),
            scroll: 0,
          });
        })
        .catch((e) =>
          setBody({
            name: row.name,
            text: (e as Error).message ?? String(e),
            scroll: 0,
          }),
        );
    };

    useKeypress(
      (input, key) => {
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.tab) return false;
        if (body) {
          const lines = body.text.split("\n").length;
          if (key.escape) {
            setBody(null);
            return true;
          }
          if (key.downArrow || input === "j") {
            setBody({
              ...body,
              scroll: Math.min(
                Math.max(0, lines - BODY_LINES),
                body.scroll + 1,
              ),
            });
            return true;
          }
          if (key.upArrow || input === "k") {
            setBody({ ...body, scroll: Math.max(0, body.scroll - 1) });
            return true;
          }
          if (key.pageDown) {
            setBody({
              ...body,
              scroll: Math.min(
                Math.max(0, lines - BODY_LINES),
                body.scroll + BODY_LINES,
              ),
            });
            return true;
          }
          if (key.pageUp) {
            setBody({ ...body, scroll: Math.max(0, body.scroll - BODY_LINES) });
            return true;
          }
          return true;
        }
        if (key.escape) {
          if (query) {
            setQuery("");
            setIndex(0);
            return true;
          }
          onUnfocus();
          return true;
        }
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
          if (row) read(row);
          return true;
        }
        if (key.backspace || key.delete) {
          setQuery((prev) => prev.slice(0, -1));
          setIndex(0);
          return true;
        }
        if (input && !key.ctrl && !key.meta) {
          setQuery((prev) => prev + input);
          setIndex(0);
          return true;
        }
        return true;
      },
      { isActive: focused, priority: KeypressPriority.High },
    );

    if (!focused) return null;

    if (body) {
      const lines = body.text.split("\n");
      const visible = lines.slice(body.scroll, body.scroll + BODY_LINES);
      return (
        <Box
          flexDirection="column"
          paddingX={1}
          borderStyle="round"
          borderColor={Theme.stateActive}
        >
          <Box>
            <Text color={Theme.textChrome}>Skill </Text>
            <Text color={Theme.textPrimary} bold>
              {body.name}
            </Text>
            <Text color={Theme.textChrome}>
              {"  "}
              {body.scroll + 1}–
              {Math.min(body.scroll + BODY_LINES, lines.length)} of{" "}
              {lines.length}
            </Text>
          </Box>
          {visible.map((line, i) => (
            <Text key={i} color={Theme.textPrimary} wrap="truncate-end">
              {line || " "}
            </Text>
          ))}
          <Text color={Theme.textChrome}>↑↓ scroll · pgup/pgdn · esc back</Text>
        </Box>
      );
    }

    const start = windowStart(cursor, rows.length, WINDOW);
    const visible = rows.slice(start, start + WINDOW);
    const nameWidth = nameColumnWidth(all ?? []);
    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.stateActive}
      >
        <Box>
          <Text color={Theme.textChrome}>Skills </Text>
          {all === null && !error ? (
            <Text color={Theme.stateActive}>
              <Spinner type="dots" />
            </Text>
          ) : (
            <Text color={Theme.textChrome}>
              {rows.length}
              {query ? ` of ${all?.length ?? 0}` : ""}
            </Text>
          )}
          {rows.length > WINDOW && (
            <Text color={Theme.textChrome}>
              {"  "}
              {start + 1}–{Math.min(start + WINDOW, rows.length)}
            </Text>
          )}
        </Box>
        {query ? (
          <Box>
            <Text color={Theme.textChrome}>filter </Text>
            <Text color={Theme.textPrimary}>{query}</Text>
            <Text color={Theme.stateActive}>█</Text>
          </Box>
        ) : null}
        {error && (
          <Text color={Theme.stateFailed}>skills unavailable: {error}</Text>
        )}
        {visible.map((row, i) => (
          // One row per skill, always: the name column is padded to the
          // longest name and the description is cut at the box edge, so
          // the drawer's height is its row count, never its prose.
          <SelectableRow key={row.path} selected={start + i === cursor}>
            <Fixed width={nameWidth}>
              <RowText
                color={row.error ? Theme.stateFailed : Theme.textPrimary}
                wrap="truncate-end"
              >
                {row.name}
              </RowText>
            </Fixed>
            <Clip>
              <RowText color={Theme.textChrome} wrap="truncate-end">
                {"  "}
                {oneLine(row.error ? `broken: ${row.error}` : row.description)}
              </RowText>
            </Clip>
          </SelectableRow>
        ))}
        {all !== null && rows.length === 0 && !error && (
          <Text color={Theme.textChrome}>
            {query ? `nothing matches "${query}"` : "no skills available"}
          </Text>
        )}
        <Text color={Theme.textChrome}>
          type to filter · ↑↓ select · enter read · esc close
        </Text>
      </Box>
    );
  },
);

SkillsPanel.displayName = "SkillsPanel";
