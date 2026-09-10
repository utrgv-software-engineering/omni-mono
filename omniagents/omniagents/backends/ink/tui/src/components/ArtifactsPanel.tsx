import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Text, useStdout } from "ink";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { useArtifacts } from "../store/appStore.js";
import {
  artifactMeta,
  defaultFileName,
  isBinary,
  previewLines,
  saveBuffer,
  type ArtifactEntry,
} from "./artifact-model.js";
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

/**
 * The artifacts drawer.
 *
 * `ui.add_artifact` had a type declaration in this codebase and no
 * handler, so an agent that produced a document produced nothing the TUI
 * could show. This is the surface for it — deliberately a drawer reached
 * from the which-key menu rather than a sixth tab: the tab bar is the
 * thing that keeps the TUI legible, and artifacts are visited, not
 * lived in.
 *
 * Two modes: the list, and a preview of one entry. Binary artifacts
 * (`image` / `pdf`, which arrive as base64 data URLs) are never
 * previewed — printing a megabyte of base64 into a TTY does not degrade
 * the rendering, it wrecks the session. They are described, and saved.
 */

const MAX_LIST_WINDOW = 6;
const PREVIEW_SCROLL = 6;
/** Narrowest wrap width worth rendering; the model honors what it is given. */
const MIN_PREVIEW_WIDTH = 20;

type Props = {
  focused: boolean;
  onUnfocus: () => void;
  /**
   * Where `w` writes. The session's workspace root when one is known,
   * else the TUI's cwd — never a path derived from the artifact title,
   * which is agent-authored text.
   */
  saveDir?: string;
};

export const ArtifactsPanel = themedMemo<Props>(
  ({ focused, onUnfocus, saveDir }) => {
    const artifacts = useArtifacts();
    const { stdout } = useStdout();
    const width = stdout?.columns ?? 80;

    const [index, setIndex] = useState(0);
    const [previewing, setPreviewing] = useState(false);
    const [scroll, setScroll] = useState(0);
    const previewRows = useDrawerBudget();
    const listWindow = Math.min(MAX_LIST_WINDOW, previewRows);
    const [statusLine, setStatusLine] = useState<string | null>(null);

    // The list grows underneath the cursor as the agent emits; clamp
    // rather than let the selection point past the end.
    useEffect(() => {
      setIndex((prev) =>
        artifacts.length === 0 ? 0 : Math.min(prev, artifacts.length - 1),
      );
    }, [artifacts.length]);

    useEffect(() => {
      if (!focused) {
        setPreviewing(false);
        setScroll(0);
        setStatusLine(null);
      }
    }, [focused]);

    const selected: ArtifactEntry | undefined = artifacts[index];

    const previewWidth = Math.max(MIN_PREVIEW_WIDTH, width - 6);
    const lines = useMemo(() => {
      if (!selected || isBinary(selected)) return [];
      // Bounded: enough to scroll through a long document without
      // materializing an unbounded array from agent-supplied content.
      return previewLines(selected.content, previewWidth, 2000);
    }, [selected, previewWidth]);

    const save = useCallback(async () => {
      if (!selected) return;
      const target = path.join(
        saveDir ?? process.cwd(),
        defaultFileName(selected),
      );
      try {
        await writeFile(target, saveBuffer(selected));
        setStatusLine(`saved ${target}`);
      } catch (e) {
        setStatusLine(`save failed: ${(e as Error).message ?? String(e)}`);
      }
    }, [selected, saveDir]);

    useKeypress(
      (input, key) => {
        // Chords and Tab belong to the global layer — this drawer must
        // never swallow Ctrl+C or screen switching.
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.tab) return false;

        if (key.escape) {
          if (previewing) {
            setPreviewing(false);
            setScroll(0);
          } else {
            onUnfocus();
          }
          return true;
        }
        if (artifacts.length === 0) return true;

        if (input === "w") {
          void save();
          return true;
        }

        if (previewing) {
          const maxScroll = Math.max(0, lines.length - previewRows);
          if (key.upArrow || input === "k") {
            setScroll((prev) => Math.max(0, prev - 1));
            return true;
          }
          if (key.downArrow || input === "j") {
            setScroll((prev) => Math.min(maxScroll, prev + 1));
            return true;
          }
          if (key.pageUp) {
            setScroll((prev) => Math.max(0, prev - PREVIEW_SCROLL));
            return true;
          }
          if (key.pageDown) {
            setScroll((prev) => Math.min(maxScroll, prev + PREVIEW_SCROLL));
            return true;
          }
          return true;
        }

        if (key.upArrow || input === "k") {
          setIndex((prev) => (prev > 0 ? prev - 1 : prev));
          return true;
        }
        if (key.downArrow || input === "j") {
          setIndex((prev) => (prev < artifacts.length - 1 ? prev + 1 : prev));
          return true;
        }
        if (key.return) {
          // Binary has nothing safe to show; `w` is the whole
          // interaction, so opening an empty pane would only mislead.
          if (selected && !isBinary(selected)) {
            setPreviewing(true);
            setScroll(0);
          } else if (selected) {
            setStatusLine("binary artifact — press w to save it");
          }
          return true;
        }
        return true; // focused drawer — swallow the rest
      },
      { isActive: focused, priority: KeypressPriority.High },
    );

    if (!focused) return null;

    if (artifacts.length === 0) {
      return (
        <Box
          flexDirection="column"
          paddingX={1}
          borderStyle="round"
          borderColor={Theme.borderAmbient}
        >
          <Text color={Theme.textChrome}>
            no artifacts yet — they appear when the agent calls display_artifact
          </Text>
          <Text color={Theme.textChrome}>esc close</Text>
        </Box>
      );
    }

    const start = windowStart(index, artifacts.length, listWindow);
    const visible = artifacts.slice(start, start + listWindow);
    const visibleLines = lines.slice(scroll, scroll + previewRows);

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.borderFocus}
      >
        <Box>
          <Text color={Theme.textChrome}>Artifacts </Text>
          <Text color={Theme.stateActive}>{artifacts.length}</Text>
          {artifacts.length > listWindow && (
            <Text color={Theme.textChrome}>
              {"  "}
              {start + 1}–{Math.min(start + listWindow, artifacts.length)} of{" "}
              {artifacts.length}
            </Text>
          )}
        </Box>

        {!previewing &&
          visible.map((entry, i) => {
            const position = start + i;
            const isSelected = position === index;
            return (
              <SelectableRow
                key={`${entry.artifactId ?? "anon"}-${position}`}
                selected={isSelected}
              >
                <Clip>
                  <RowText color={Theme.textPrimary} wrap="truncate-end">
                    {oneLine(entry.title || "(untitled)")}
                  </RowText>
                </Clip>
                <Fixed>
                  <RowText color={Theme.textChrome}>
                    {" "}
                    · {artifactMeta(entry)}
                  </RowText>
                </Fixed>
              </SelectableRow>
            );
          })}

        {previewing && selected && (
          <Box flexDirection="column">
            <Box>
              <Text color={Theme.textPrimary} bold>
                {selected.title || "(untitled)"}
              </Text>
              <Text color={Theme.textChrome}>
                {"  "}
                {Math.min(scroll + previewRows, lines.length)}/{lines.length}
              </Text>
            </Box>
            {visibleLines.map((line, i) => (
              <Text
                key={scroll + i}
                color={Theme.textSecondary}
                wrap="truncate-end"
              >
                {line === "" ? " " : line}
              </Text>
            ))}
          </Box>
        )}

        {statusLine && (
          <Text color={Theme.stateBlocked} italic>
            {statusLine}
          </Text>
        )}

        <Text color={Theme.textChrome}>
          {previewing
            ? "↑↓ scroll · w save · esc back"
            : "↑↓ select · enter preview · w save · esc close"}
        </Text>
      </Box>
    );
  },
);

ArtifactsPanel.displayName = "ArtifactsPanel";
