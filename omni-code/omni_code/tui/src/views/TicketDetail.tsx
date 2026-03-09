/**
 * TicketDetail — tiling-based ticket view.
 *
 * Renders a split layout with the supervisor chat as the primary pane
 * and content panels (info/log/artifacts/diff) alongside it.
 * PTY sessions and tiling state are managed by App.tsx, just like ProjectHome.
 */

import React, { useCallback, useEffect, useState } from "react";
import { Box, Text } from "ink";
import type { Column, Ticket } from "../rpc.js";
import { api } from "../rpc.js";
import { Panel } from "../components/Panel.js";
import { TicketTileTree } from "../tiling/TicketTileTree.js";
import { getLeaves, type TileNode, type TileType } from "../tiling/tree.js";
import type { PtySession } from "../tiling/PtySession.js";
import { LogViewer } from "./LogViewer.js";
import { ArtifactBrowser } from "./ArtifactBrowser.js";
import { DiffViewer } from "./DiffViewer.js";
import type { Hint } from "../components/KeyHint.js";
import { useTheme } from "../ThemeContext.js";

// --- Split picker for ticket tiles ---

type TicketSplitOption = {
  tileType: TileType;
  key: string;
  label: string;
  /** Only one instance allowed at a time. */
  singleton?: boolean;
};

const TICKET_SPLIT_OPTIONS: TicketSplitOption[] = [
  { tileType: "supervisor", key: "s", label: "Supervisor chat", singleton: true },
  { tileType: "ssh", key: "h", label: "SSH terminal", singleton: true },
  { tileType: "info", key: "i", label: "Info" },
  { tileType: "log", key: "l", label: "Log" },
  { tileType: "artifacts", key: "a", label: "Artifacts" },
  { tileType: "diff", key: "d", label: "Diff" },
];

function TicketSplitPicker({ isEmpty, tilingRoot }: { isEmpty: boolean; tilingRoot: TileNode | null }) {
  const t = useTheme();
  const existingTypes = new Set(tilingRoot ? getLeaves(tilingRoot).map((l) => l.tileType) : []);

  return (
    <Panel title="New Split" focused={true} focusedColor={t.success} flexGrow={1}>
      <Box flexDirection="column" paddingX={1} paddingY={1}>
        <Box marginBottom={1}>
          <Text color={t.fg} bold>{isEmpty ? "Choose what to open:" : "Split with:"}</Text>
        </Box>
        {TICKET_SPLIT_OPTIONS.map((opt) => {
          const disabled = opt.singleton && existingTypes.has(opt.tileType);
          return (
            <Box key={opt.tileType}>
              <Text color={disabled ? t.fgSubtle : undefined}>
                {"  "}<Text color={disabled ? t.fgSubtle : t.accent} bold>{opt.key}</Text>{" "}{opt.label}
                {disabled ? <Text color={t.fgSubtle}> (active)</Text> : null}
              </Text>
            </Box>
          );
        })}
        <Box marginTop={1}>
          <Text color={t.fgSubtle}>
            {isEmpty
              ? "Press a key to open, or Esc to go back."
              : "Press a key to split, or Esc to cancel."}
          </Text>
        </Box>
      </Box>
    </Panel>
  );
}

// --- Component ---

type Props = {
  ticket: Ticket;
  projectId: string;
  // Tiling state (managed by App.tsx)
  tilingRoot: TileNode | null;
  focusedTileId: string | null;
  active: boolean;
  insertMode: boolean;
  sessions: Map<string, PtySession>;
  pickerOpen: boolean;
  availCols: number;
  availRows: number;
  // Callbacks
  onBack: () => void;
  onQuit: () => void;
  onInputActive?: (active: boolean) => void;
  onHints?: (hints: Hint[]) => void;
  onStatus?: (status: string | null) => void;
};

export function TicketDetail({
  ticket: initialTicket, projectId,
  tilingRoot, focusedTileId, active, insertMode, sessions, pickerOpen,
  availCols, availRows,
  onBack: _onBack, onQuit: _onQuit, onInputActive: _onInputActive, onHints: _onHints, onStatus,
}: Props) {
  const t = useTheme();
  const [ticket, setTicket] = useState<Ticket>(initialTicket);
  const [columns, setColumns] = useState<Column[]>([]);
  const [status, setStatus] = useState<string | null>(null);

  // Refresh ticket data
  const refresh = useCallback(async () => {
    try {
      const tickets = await api.listTickets(projectId);
      const updated = tickets.find((t) => t.id === ticket.id);
      if (updated) setTicket(updated);
      const pipeline = await api.getPipeline(projectId);
      setColumns(pipeline.columns);
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [projectId, ticket.id]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  // Sync initial ticket prop
  useEffect(() => { setTicket(initialTicket); }, [initialTicket.id]);

  useEffect(() => { onStatus?.(status ?? null); }, [status]);
  useEffect(() => {
    if (!status) return;
    const timer = setTimeout(() => setStatus(null), 4000);
    return () => clearTimeout(timer);
  }, [status]);

  // Render content tiles (non-PTY) — passed as render prop to TicketTileTree
  const renderContentTile = useCallback(
    (tileType: TileType, _innerCols: number, _innerRows: number, focused: boolean) => {
      const noop = () => {};
      switch (tileType) {
        case "log":
          return (
            <LogViewer
              ticketId={ticket.id}
              ticketTitle={ticket.title}
              projectId={projectId}
              onBack={noop}
              onQuit={noop}
              active={focused}
              embedded
            />
          );
        case "artifacts":
          return (
            <ArtifactBrowser
              ticketId={ticket.id}
              ticketTitle={ticket.title}
              onBack={noop}
              onQuit={noop}
              active={focused}
              embedded
            />
          );
        case "diff":
          return (
            <DiffViewer
              ticketId={ticket.id}
              ticketTitle={ticket.title}
              projectId={projectId}
              onBack={noop}
              onQuit={noop}
              active={focused}
              embedded
            />
          );
        default:
          return <Text color={t.fgSubtle}> {tileType}</Text>;
      }
    },
    [ticket.id, ticket.title, projectId],
  );

  const showPicker = pickerOpen || !tilingRoot;

  if (showPicker && !tilingRoot) {
    return <TicketSplitPicker isEmpty={true} tilingRoot={null} />;
  }

  if (showPicker && tilingRoot) {
    return (
      <Box flexGrow={1} flexDirection="row">
        <Box flexGrow={1}>
          <TicketTileTree
            node={tilingRoot}
            availCols={Math.max(10, availCols - 30)}
            availRows={availRows}
            focusedId={focusedTileId}
            active={active}
            rawFocused={insertMode}
            sessions={sessions}
            ticket={ticket}
            projectId={projectId}
            columns={columns}
            renderContentTile={renderContentTile}
          />
        </Box>
        <Box width={28} flexShrink={0}>
          <TicketSplitPicker isEmpty={false} tilingRoot={tilingRoot} />
        </Box>
      </Box>
    );
  }

  return (
    <Box flexGrow={1}>
      <TicketTileTree
        node={tilingRoot!}
        availCols={availCols}
        availRows={availRows}
        focusedId={focusedTileId}
        active={active}
        rawFocused={insertMode}
        sessions={sessions}
        ticket={ticket}
        projectId={projectId}
        columns={columns}
        renderContentTile={renderContentTile}
      />
    </Box>
  );
}
