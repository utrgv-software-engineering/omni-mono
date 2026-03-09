import React, { useCallback, useEffect, useState } from "react";
import { Box, Text, useInput, useStdout, type Key } from "ink";
import { api } from "../rpc.js";
import { Panel } from "../components/Panel.js";
import { HelpOverlay } from "../components/HelpOverlay.js";
import type { Hint } from "../components/KeyHint.js";

type ArtifactEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
};

type Props = {
  ticketId: string;
  ticketTitle: string;
  onBack: () => void;
  onQuit: () => void;
  active?: boolean;
  embedded?: boolean;
  onHints?: (hints: Hint[]) => void;
  onStatus?: (status: string | null) => void;
};

const HELP_HINTS: Hint[] = [
  { key: "j/k", label: "navigate files" },
  { key: "enter", label: "open directory or preview file" },
  { key: "O", label: "open file externally (xdg-open)" },
  { key: "bksp", label: "go up one directory" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "esc", label: "back to ticket" },
];

const BAR_HINTS: Hint[] = [
  { key: "j/k", label: "navigate" },
  { key: "enter", label: "open" },
  { key: "O", label: "external" },
  { key: "bksp", label: "up dir" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "esc", label: "back" },
];

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}

export function ArtifactBrowser({ ticketId, ticketTitle, onBack, onQuit, active = true, embedded = false, onHints, onStatus }: Props) {
  const [entries, setEntries] = useState<ArtifactEntry[]>([]);
  const [selected, setSelected] = useState(0);
  const [currentDir, setCurrentDir] = useState<string | undefined>(undefined);
  const [preview, setPreview] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showHelp, setShowHelp] = useState(false);
  const { stdout } = useStdout();
  const previewLines = (stdout?.rows ?? 24) - 10;

  const refresh = useCallback(async () => {
    try {
      const result = await api.ticketArtifacts(ticketId, currentDir);
      setEntries(result);
      setSelected(0);
      setPreview(null);
      setLoading(false);
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setLoading(false);
    }
  }, [ticketId, currentDir]);

  useEffect(() => { setLoading(true); refresh(); }, [refresh]);

  useEffect(() => {
    if (!status) return;
    const timer = setTimeout(() => setStatus(null), 4000);
    return () => clearTimeout(timer);
  }, [status]);

  // Report status/hints to parent for app-level StatusBar
  useEffect(() => { onStatus?.(status ?? null); }, [status]);
  useEffect(() => { onHints?.(BAR_HINTS); }, []);

  const loadPreview = useCallback(async (entry: ArtifactEntry) => {
    try {
      const result = await api.ticketArtifactRead(ticketId, entry.path) as { content: string | null; mime_type?: string; size?: number };
      if (result.content === null) {
        const mime = result.mime_type ?? "unknown";
        const size = result.size != null ? formatSize(result.size) : "?";
        setPreview(`[binary: ${mime}, ${size}]`);
      } else {
        const lines = result.content.split("\n");
        setPreview(lines.slice(0, previewLines).join("\n"));
      }
    } catch (err) {
      setPreview(`(cannot preview: ${err instanceof Error ? err.message : String(err)})`);
    }
  }, [ticketId, previewLines]);

  useInput((input: string, key: Key) => {
    if (!active) return;
    if (showHelp) return;
    if (input === "?") { setShowHelp(true); return; }
    if (key.escape) {
      if (preview) { setPreview(null); return; }
      onBack();
      return;
    }
    if (input === "q") { onQuit(); return; }
    if ((key.upArrow || input === "k") && entries.length > 0) { setSelected((v) => Math.max(0, v - 1)); setPreview(null); return; }
    if ((key.downArrow || input === "j") && entries.length > 0) { setSelected((v) => Math.min(entries.length - 1, v + 1)); setPreview(null); return; }
    if (key.return && entries.length > 0) {
      const entry = entries[selected];
      if (entry.is_dir) setCurrentDir(entry.path);
      else loadPreview(entry);
      return;
    }
    if (key.backspace || key.delete) {
      if (!currentDir) return;
      const parts = currentDir.split("/");
      parts.pop();
      setCurrentDir(parts.length > 0 ? parts.join("/") : undefined);
      setPreview(null);
      return;
    }
    if (input === "O" && entries.length > 0) {
      const entry = entries[selected];
      if (!entry.is_dir) {
        api.ticketArtifactOpen(ticketId, entry.path)
          .then(() => setStatus(`Opened ${entry.name} externally`))
          .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      }
      return;
    }
    if (input === "r") { refresh(); setStatus("Refreshed"); return; }
  });

  if (showHelp) {
    return (
      <Box flexDirection="column" flexGrow={1}>
        <HelpOverlay title="Artifacts" hints={HELP_HINTS} onClose={() => setShowHelp(false)} />
      </Box>
    );
  }

  const pathLabel = currentDir ? ` / ${currentDir}` : "";

  const fileList = (
    <Box flexDirection="column" paddingX={1} flexGrow={1}>
      {loading ? (
        <Text color="gray">Loading...</Text>
      ) : entries.length === 0 ? (
        <Text color="gray">No artifacts found.</Text>
      ) : (
        entries.map((entry, i) => {
          const isSelected = i === selected;
          return (
            <Text key={entry.path} color={isSelected ? "cyan" : undefined} wrap="truncate">
              {isSelected ? "\u25B8 " : "  "}
              {entry.is_dir ? `${entry.name}/` : entry.name}
              {!entry.is_dir ? <Text color="gray"> ({formatSize(entry.size)})</Text> : null}
            </Text>
          );
        })
      )}
    </Box>
  );

  if (embedded) {
    return (
      <Box flexDirection="row" flexGrow={1}>
        {fileList}
        {preview !== null ? (
          <Box flexDirection="column" paddingX={1} flexGrow={2} borderStyle="single" borderColor="gray" borderLeft borderTop={false} borderBottom={false} borderRight={false}>
            <Text bold color="gray">Preview: {entries[selected]?.name}</Text>
            <Text>{preview}</Text>
          </Box>
        ) : null}
      </Box>
    );
  }

  return (
    <Box flexDirection="column" flexGrow={1}>
      <Box flexDirection="row" flexGrow={1}>
        <Panel title={`Artifacts${pathLabel}`} focused={!preview} flexGrow={1}>
          {fileList}
        </Panel>
        {preview !== null ? (
          <Panel title={`Preview: ${entries[selected]?.name}`} focused flexGrow={2}>
            <Box flexDirection="column" paddingX={1}>
              <Text>{preview}</Text>
            </Box>
          </Panel>
        ) : null}
      </Box>
    </Box>
  );
}
