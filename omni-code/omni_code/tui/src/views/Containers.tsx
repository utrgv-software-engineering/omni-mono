import React, { useCallback, useEffect, useState } from "react";
import { Box, Text, useInput, type Key } from "ink";
import type { Container } from "../rpc.js";
import { api } from "../rpc.js";
import { Panel } from "../components/Panel.js";
import { StatusBar } from "../components/StatusBar.js";
import { HelpOverlay } from "../components/HelpOverlay.js";
import { ConfirmDialog } from "../components/ConfirmDialog.js";
import type { Hint } from "../components/KeyHint.js";
import { useTheme } from "../ThemeContext.js";
import type { Theme } from "../theme.js";

type Props = {
  onQuit: () => void;
};

function clamp(value: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(value, max - 1));
}

function stateColor(state: string, t: Theme): string {
  if (state === "running") return t.success;
  if (state === "exited") return t.error;
  if (state === "created") return t.warning;
  if (state === "paused") return t.info;
  return t.fgSubtle;
}

const HELP_HINTS: Hint[] = [
  { key: "j/k", label: "navigate containers" },
  { key: "s", label: "start container" },
  { key: "x", label: "stop container" },
  { key: "R", label: "rebuild (remove) container" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "q", label: "back" },
];

const BAR_HINTS: Hint[] = [
  { key: "j/k", label: "navigate" },
  { key: "s", label: "start" },
  { key: "x", label: "stop" },
  { key: "R", label: "remove" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "esc/q", label: "back" },
];

export function Containers({ onQuit }: Props) {
  const t = useTheme();
  const [containers, setContainers] = useState<Container[]>([]);
  const [selected, setSelected] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showHelp, setShowHelp] = useState(false);
  const [confirmAction, setConfirmAction] = useState<{ message: string; action: () => void } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const result = await api.listContainers();
      setContainers(result);
      setLoading(false);
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  useEffect(() => {
    if (!status) return;
    const timer = setTimeout(() => setStatus(null), 4000);
    return () => clearTimeout(timer);
  }, [status]);

  const current = containers[selected] ?? null;

  useInput((input: string, key: Key) => {
    if (showHelp || confirmAction) return;
    if (input === "?") { setShowHelp(true); return; }
    if (input === "q" || key.escape) { onQuit(); return; }
    if (input === "r") { refresh(); setStatus("Refreshed"); return; }
    if (key.upArrow || input === "k") { setSelected((v) => clamp(v - 1, containers.length)); return; }
    if (key.downArrow || input === "j") { setSelected((v) => clamp(v + 1, containers.length)); return; }
    if (input === "s" && current) {
      setStatus(`Starting ${current.name}...`);
      api.startContainer(current.name)
        .then(() => { setStatus(`Started ${current.name}`); refresh(); })
        .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    if (input === "x" && current) {
      const c = current;
      setConfirmAction({
        message: `Stop container "${c.name}"?`,
        action: () => {
          setStatus(`Stopping ${c.name}...`);
          api.stopContainer(c.name)
            .then(() => { setStatus(`Stopped ${c.name}`); refresh(); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
        },
      });
      return;
    }
    if (input === "R" && current) {
      const c = current;
      setConfirmAction({
        message: `Remove container "${c.name}"?`,
        action: () => {
          setStatus(`Removing ${c.name}...`);
          api.rebuildContainer(c.name)
            .then(() => { setStatus(`Removed ${c.name}`); refresh(); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
        },
      });
      return;
    }
  });

  if (showHelp) {
    return (
      <Box flexDirection="column" flexGrow={1}>
        <HelpOverlay title="Containers" hints={HELP_HINTS} onClose={() => setShowHelp(false)} />
      </Box>
    );
  }

  return (
    <Box flexDirection="column" flexGrow={1}>
      <Box flexDirection="row" flexGrow={1}>
        {/* Container list */}
        <Panel title={`Containers (${containers.length})`} focused flexGrow={1}>
          <Box flexDirection="column" paddingX={1}>
            {loading ? (
              <Text color={t.fgSubtle}>Loading containers...</Text>
            ) : containers.length === 0 ? (
              <Text color={t.fgSubtle}>No omni containers found.</Text>
            ) : (
              containers.map((c, i) => {
                const isSelected = i === selected;
                return (
                  <Text key={c.name} color={isSelected ? t.accent : undefined} wrap="truncate">
                    {isSelected ? "\u25B8 " : "  "}
                    <Text color={stateColor(c.state, t)} bold>{c.state.padEnd(8)}</Text>
                    {"  "}{c.name}
                  </Text>
                );
              })
            )}
          </Box>
        </Panel>

        {/* Detail panel */}
        {current && !loading ? (
          <Panel title="Detail" flexGrow={1}>
            <Box flexDirection="column" paddingX={1}>
              <Box><Text color={t.fgSubtle}>Name    </Text><Text color={t.fg}>{current.name}</Text></Box>
              <Box><Text color={t.fgSubtle}>Image   </Text><Text color={t.fg}>{current.image}</Text></Box>
              <Box><Text color={t.fgSubtle}>Status  </Text><Text color={t.fg}>{current.status}</Text></Box>
              <Box><Text color={t.fgSubtle}>Created </Text><Text color={t.fg}>{current.created}</Text></Box>
              {current.ports ? <Box><Text color={t.fgSubtle}>Ports   </Text><Text color={t.fg}>{current.ports}</Text></Box> : null}
            </Box>
          </Panel>
        ) : null}
      </Box>

      {confirmAction ? (
        <Box paddingX={1}>
          <ConfirmDialog
            message={confirmAction.message}
            onConfirm={() => { confirmAction.action(); setConfirmAction(null); }}
            onCancel={() => setConfirmAction(null)}
          />
        </Box>
      ) : null}

      <StatusBar hints={BAR_HINTS} status={status} />
    </Box>
  );
}
