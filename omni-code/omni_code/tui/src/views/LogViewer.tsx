import React, { useCallback, useEffect, useState } from "react";
import { Box, Text, useInput, useStdout, type Key } from "ink";
import { api } from "../rpc.js";
import { Panel } from "../components/Panel.js";
import { HelpOverlay } from "../components/HelpOverlay.js";
import type { Hint } from "../components/KeyHint.js";

type HistoryMessage = {
  index: number;
  role: string;
  content: string;
};

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

const ROLE_COLORS: Record<string, string> = {
  user: "cyan",
  assistant: "green",
  tool: "yellow",
  system: "magenta",
};

const HELP_HINTS: Hint[] = [
  { key: "j/k", label: "navigate messages" },
  { key: "g", label: "jump to top" },
  { key: "G", label: "jump to bottom" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "esc", label: "back to ticket" },
];

const BAR_HINTS: Hint[] = [
  { key: "j/k", label: "navigate" },
  { key: "g/G", label: "top/bottom" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "esc", label: "back" },
];

export function LogViewer({ ticketId, ticketTitle, projectId, onBack, onQuit, active = true, embedded = false, onHints, onStatus }: Props) {
  const [messages, setMessages] = useState<HistoryMessage[]>([]);
  const [selected, setSelected] = useState(0);
  const [scrollOffset, setScrollOffset] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showHelp, setShowHelp] = useState(false);
  const { stdout } = useStdout();
  const visibleRows = (stdout?.rows ?? 24) - 6;

  const refresh = useCallback(async () => {
    try {
      const result = await api.ticketHistory(ticketId, projectId, 200);
      setMessages(result);
      setLoading(false);
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setLoading(false);
    }
  }, [ticketId, projectId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  useEffect(() => {
    if (!status) return;
    const timer = setTimeout(() => setStatus(null), 4000);
    return () => clearTimeout(timer);
  }, [status]);

  // Report status/hints to parent for app-level StatusBar
  useEffect(() => { onStatus?.(status ?? null); }, [status]);
  useEffect(() => { onHints?.(BAR_HINTS); }, []);

  useInput((input: string, key: Key) => {
    if (!active) return;
    if (showHelp) return;
    if (input === "?") { setShowHelp(true); return; }
    if (key.escape) { onBack(); return; }
    if (input === "q") { onQuit(); return; }
    if (input === "r") { setLoading(true); refresh(); setStatus("Refreshed"); return; }
    if ((key.upArrow || input === "k") && messages.length > 0) {
      setSelected((v) => {
        const next = Math.max(0, v - 1);
        if (next < scrollOffset) setScrollOffset(next);
        return next;
      });
      return;
    }
    if ((key.downArrow || input === "j") && messages.length > 0) {
      setSelected((v) => {
        const next = Math.min(messages.length - 1, v + 1);
        if (next >= scrollOffset + visibleRows) setScrollOffset(next - visibleRows + 1);
        return next;
      });
      return;
    }
    if (input === "G") {
      const last = messages.length - 1;
      setSelected(last);
      setScrollOffset(Math.max(0, last - visibleRows + 1));
      return;
    }
    if (input === "g") { setSelected(0); setScrollOffset(0); return; }
  });

  if (showHelp) {
    return (
      <Box flexDirection="column" flexGrow={1}>
        <HelpOverlay title="Log Viewer" hints={HELP_HINTS} onClose={() => setShowHelp(false)} />
      </Box>
    );
  }

  const visibleMessages = messages.slice(scrollOffset, scrollOffset + visibleRows);

  const content = (
    <Box flexDirection="column" paddingX={1} flexGrow={1}>
      {loading && messages.length === 0 ? (
        <Text color="gray">Loading history...</Text>
      ) : messages.length === 0 ? (
        <Text color="gray">No history found.</Text>
      ) : (
        visibleMessages.map((msg, i) => {
          const actualIndex = scrollOffset + i;
          const isSelected = actualIndex === selected;
          const roleColor = ROLE_COLORS[msg.role] ?? "white";
          const preview = isSelected
            ? msg.content
            : msg.content.length > 120 ? msg.content.slice(0, 120) + "\u2026" : msg.content;
          const lines = preview.split("\n");
          const displayContent = isSelected ? lines.join("\n") : lines[0] + (lines.length > 1 ? " \u2026" : "");

          return (
            <Text key={msg.index} color={isSelected ? "cyan" : undefined} wrap="truncate">
              {isSelected ? "\u25B8 " : "  "}
              <Text color={roleColor} bold>[{msg.role}]</Text>
              {" "}{displayContent}
            </Text>
          );
        })
      )}
    </Box>
  );

  if (embedded) return content;

  return (
    <Box flexDirection="column" flexGrow={1}>
      <Panel title={`Log \u2014 ${ticketTitle} (${messages.length})`} focused flexGrow={1}>
        {content}
      </Panel>
    </Box>
  );
}
