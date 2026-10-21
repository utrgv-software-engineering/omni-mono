import React, { useMemo, useState } from "react";
import { Box, Text } from "ink";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { windowStart } from "./model-picker-model.js";
import { RowText, SelectableRow } from "./shared/SelectableRow.js";
import { Clip, Fixed, useDrawerBudget } from "./shared/drawer-layout.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";
import { rolesFromPalette } from "../theme/roles.js";
import {
  activeThemeId,
  availableThemes,
  selectTheme,
} from "../theme/controller.js";
import type { ThemeSource } from "../theme/sources.js";
import {
  filterThemes,
  initialThemeIndex,
  originLabel,
} from "./theme-panel-model.js";

type Props = {
  focused: boolean;
  onUnfocus: () => void;
};

const MAX_WINDOW = 10;

/**
 * The theme drawer: what `/theme` opens. Themes are Omarchy palettes:
 * the desktop's current one when this machine runs Omarchy, the user's
 * own, and the bundled set. The cursor previews in a swatch strip, not
 * on the live UI, so browsing costs nothing and Esc leaves no trace;
 * Enter applies and remembers.
 */
export const ThemePanel = themedMemo(({ focused, onUnfocus }: Props) => {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const all = useMemo(() => (focused ? availableThemes() : []), [focused]);
  const rows = filterThemes(all, query);
  const cursor =
    rows.length === 0
      ? 0
      : Math.min(
          index ?? initialThemeIndex(rows, activeThemeId()),
          rows.length - 1,
        );
  const budget = useDrawerBudget();
  const window = Math.max(3, Math.min(MAX_WINDOW, budget - 3));

  useKeypress(
    (input, key) => {
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false;
      if (key.escape) {
        if (query) {
          setQuery("");
          setIndex(null);
          return true;
        }
        setQuery("");
        setIndex(null);
        setNote(null);
        onUnfocus();
        return true;
      }
      if (key.upArrow) {
        setIndex(Math.max(0, cursor - 1));
        return true;
      }
      if (key.downArrow) {
        setIndex(Math.min(cursor + 1, rows.length - 1));
        return true;
      }
      if (key.return) {
        const row = rows[cursor];
        if (row) {
          const applied = selectTheme(row.id);
          setNote(`${applied.name} applied`);
        }
        return true;
      }
      if (key.backspace || key.delete) {
        setQuery((prev) => prev.slice(0, -1));
        setIndex(null);
        return true;
      }
      if (input) {
        setQuery((prev) => prev + input);
        setIndex(null);
        return true;
      }
      return true;
    },
    { isActive: focused, priority: KeypressPriority.High },
  );

  if (!focused) return null;

  const start = windowStart(cursor, rows.length, window);
  const visible = rows.slice(start, start + window);
  const previewed = rows[cursor];

  return (
    <Box
      flexDirection="column"
      paddingX={1}
      borderStyle="round"
      borderColor={Theme.stateActive}
    >
      <Box>
        <Text color={Theme.textChrome}>Theme </Text>
        <Text color={Theme.textChrome}>
          {rows.length}
          {query ? ` of ${all.length}` : ""}
        </Text>
        {rows.length > window && (
          <Text color={Theme.textChrome}>
            {"  "}
            {start + 1}–{Math.min(start + window, rows.length)}
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
      {visible.map((row, i) => (
        <SelectableRow key={row.id} selected={start + i === cursor}>
          <Fixed>
            <RowText color={Theme.textPrimary}>{row.name}</RowText>
            {row.id === activeThemeId() && (
              <RowText color={Theme.stateDone}> ✔</RowText>
            )}
          </Fixed>
          <Clip>
            <RowText color={Theme.textChrome} wrap="truncate-end">
              {"  "}· {originLabel(row)}
            </RowText>
          </Clip>
        </SelectableRow>
      ))}
      {rows.length === 0 && (
        <Text color={Theme.textChrome}>nothing matches "{query}"</Text>
      )}
      {previewed && <Swatches source={previewed} />}
      <Text color={Theme.textChrome}>
        {note ? `${note} · ` : ""}
        type to filter · ↑↓ preview · enter apply · esc close
      </Text>
    </Box>
  );
});

ThemePanel.displayName = "ThemePanel";

/** A strip of the candidate's roles, painted from its own palette. */
const Swatches = ({ source }: { source: ThemeSource }) => {
  const r = rolesFromPalette(source.palette);
  const bg = source.palette.background;
  return (
    <Box marginTop={1} flexDirection="column">
      <Box>
        <Text backgroundColor={bg} color={r.textPrimary}>
          {" "}
          text{" "}
        </Text>
        <Text backgroundColor={bg} color={r.textSecondary}>
          value{" "}
        </Text>
        <Text backgroundColor={bg} color={r.textChrome}>
          chrome{" "}
        </Text>
        <Text backgroundColor={bg} color={r.accentUser}>
          you{" "}
        </Text>
        <Text backgroundColor={bg} color={r.accentAgent}>
          agent{" "}
        </Text>
        <Text backgroundColor={bg} color={r.stateActive}>
          active{" "}
        </Text>
        <Text backgroundColor={bg} color={r.stateDone}>
          done{" "}
        </Text>
        <Text backgroundColor={bg} color={r.stateFailed}>
          failed{" "}
        </Text>
        <Text backgroundColor={bg} color={r.stateBlocked}>
          blocked{" "}
        </Text>
        <Text backgroundColor={source.palette.selection} color={r.textPrimary}>
          {" "}
          selected{" "}
        </Text>
        <Text backgroundColor={r.barActiveBg} color={r.barActiveFg}>
          {" "}
          tab{" "}
        </Text>
      </Box>
    </Box>
  );
};
