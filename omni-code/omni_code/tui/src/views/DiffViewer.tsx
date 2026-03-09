import React, { useCallback, useEffect, useState } from "react";
import { Box, Text, useInput, useStdout, type Key } from "ink";
import type { DiffResponse, FileDiff } from "../rpc.js";
import { api } from "../rpc.js";
import { Panel } from "../components/Panel.js";
import { HelpOverlay } from "../components/HelpOverlay.js";
import type { Hint } from "../components/KeyHint.js";

type Props = {
  ticketId: string;
  ticketTitle: string;
  projectId: string;
  onBack: () => void;
  onQuit: () => void;
  active?: boolean;
  embedded?: boolean;
  onHints?: (hints: Hint[]) => void;
  onStatus?: (status: string | null) => void;
};

function clamp(value: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(value, max - 1));
}

const HELP_HINTS: Hint[] = [
  { key: "j/k", label: "navigate files" },
  { key: "enter", label: "view file patch" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "esc", label: "back" },
];

const BAR_HINTS: Hint[] = [
  { key: "j/k", label: "navigate" },
  { key: "enter", label: "view patch" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "esc", label: "back" },
];

export function DiffViewer({ ticketId, ticketTitle, projectId, onBack, onQuit, active = true, embedded = false, onHints, onStatus }: Props) {
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [selected, setSelected] = useState(0);
  const [patch, setPatch] = useState<string | null>(null);
  const [patchScroll, setPatchScroll] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showHelp, setShowHelp] = useState(false);
  const { stdout } = useStdout();
  const visibleRows = (stdout?.rows ?? 24) - 8;

  const refresh = useCallback(async () => {
    try {
      const result = await api.gitDiff(ticketId, projectId);
      setDiff(result);
      setLoading(false);
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setLoading(false);
    }
  }, [ticketId, projectId]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (!status) return;
    const timer = setTimeout(() => setStatus(null), 4000);
    return () => clearTimeout(timer);
  }, [status]);

  // Report status/hints to parent for app-level StatusBar
  useEffect(() => { onStatus?.(status ?? null); }, [status]);
  useEffect(() => {
    onHints?.(patch !== null
      ? [{ key: "j/k", label: "scroll" }, { key: "esc", label: "back to files" }]
      : BAR_HINTS
    );
  }, [patch !== null]);

  const loadPatch = useCallback(async (file: FileDiff) => {
    try {
      const result = await api.gitDiffFile(ticketId, file.path, projectId);
      setPatch(result.patch || "(no diff available)");
      setPatchScroll(0);
    } catch (err) {
      setPatch(`(error: ${err instanceof Error ? err.message : String(err)})`);
    }
  }, [ticketId, projectId]);

  useInput((input: string, key: Key) => {
    if (!active) return;
    if (showHelp) return;
    if (input === "?") { setShowHelp(true); return; }
    if (key.escape) {
      if (patch !== null) { setPatch(null); return; }
      onBack();
      return;
    }
    if (input === "q") { onQuit(); return; }
    if (input === "r") { setLoading(true); refresh(); setStatus("Refreshed"); return; }

    if (patch !== null) {
      const patchLines = patch.split("\n");
      if (key.upArrow || input === "k") { setPatchScroll((v) => Math.max(0, v - 1)); return; }
      if (key.downArrow || input === "j") { setPatchScroll((v) => Math.min(Math.max(0, patchLines.length - visibleRows), v + 1)); return; }
      return;
    }

    const files = diff?.files ?? [];
    if (key.upArrow || input === "k") { setSelected((v) => clamp(v - 1, files.length)); return; }
    if (key.downArrow || input === "j") { setSelected((v) => clamp(v + 1, files.length)); return; }
    if (key.return && files[selected]) { loadPatch(files[selected]); return; }
  });

  if (showHelp) {
    return (
      <Box flexDirection="column" flexGrow={1}>
        <HelpOverlay title="Git Diff" hints={HELP_HINTS} onClose={() => setShowHelp(false)} />
      </Box>
    );
  }

  const diffSummary = diff
    ? `${diff.total_files} files +${diff.total_additions} -${diff.total_deletions}`
    : "";

  const patchContent = patch !== null ? (
    <Box flexDirection="column" paddingX={1} flexGrow={1}>
      {patch.split("\n").slice(patchScroll, patchScroll + visibleRows).map((line, i) => {
        let color: string | undefined;
        if (line.startsWith("+") && !line.startsWith("+++")) color = "green";
        else if (line.startsWith("-") && !line.startsWith("---")) color = "red";
        else if (line.startsWith("@@")) color = "cyan";
        return <Text key={i} color={color} wrap="truncate">{line}</Text>;
      })}
    </Box>
  ) : null;

  const fileListContent = (
    <Box flexDirection="column" paddingX={1} flexGrow={1}>
      {loading ? (
        <Text color="gray">Loading diff...</Text>
      ) : !diff || diff.files.length === 0 ? (
        <Text color="gray">No changes detected.</Text>
      ) : (
        diff.files.map((file, i) => {
          const isSelected = i === selected;
          return (
            <Text key={file.path} color={isSelected ? "cyan" : undefined} wrap="truncate">
              {isSelected ? "\u25B8 " : "  "}
              {file.untracked ? <Text color="yellow">? </Text> : file.staged ? <Text color="green">S </Text> : <Text color="blue">M </Text>}
              {file.path}
              {file.is_binary ? (
                <Text color="gray"> (binary)</Text>
              ) : (
                <Text color="gray"> <Text color="green">+{file.additions}</Text> <Text color="red">-{file.deletions}</Text></Text>
              )}
            </Text>
          );
        })
      )}
    </Box>
  );

  if (embedded) {
    return patch !== null ? patchContent : fileListContent;
  }

  return (
    <Box flexDirection="column" flexGrow={1}>
      {patch !== null ? (
        <Panel title={diff?.files[selected]?.path ?? "Patch"} focused flexGrow={1}>
          {patchContent}
        </Panel>
      ) : (
        <Panel
          title={`Diff \u2014 ${ticketTitle}`}
          badge={diffSummary ? <Text color="gray">{diffSummary}</Text> : undefined}
          focused
          flexGrow={1}
        >
          {fileListContent}
        </Panel>
      )}
    </Box>
  );
}
