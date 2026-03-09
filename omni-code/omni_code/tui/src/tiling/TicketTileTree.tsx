/**
 * TicketTileTree — recursive tiling renderer for the ticket detail view.
 *
 * Like TileTree, but knows how to render both PTY tiles (supervisor/ssh)
 * and content tiles (info/log/artifacts/diff) that show ticket data.
 */

import React from "react";
import { Box, Text } from "ink";
import { Panel } from "../components/Panel.js";
import { PTY_TILE_TYPES, type TileNode, type TileType } from "./tree.js";
import type { PtySession } from "./PtySession.js";
import { TerminalView } from "./TerminalView.js";
import type { Ticket, Column } from "../rpc.js";

// --- Content tile renderers (inline, lightweight) ---

function InfoTile({ ticket, columns, innerCols }: { ticket: Ticket; columns: Column[]; innerCols: number }) {
  const currentColumn = columns.find((c) => c.id === ticket.column_id);
  return (
    <Box flexDirection="column" paddingX={1} width={innerCols}>
      <Text bold>{ticket.title}</Text>
      <Box marginTop={1} flexDirection="column">
        <Box><Text color="gray">Column   </Text><Text>{currentColumn?.label ?? ticket.column_id}</Text></Box>
        <Box><Text color="gray">Priority </Text><Text color={
          ticket.priority === "critical" ? "red" :
          ticket.priority === "high" ? "yellow" :
          ticket.priority === "low" ? "gray" : undefined
        } bold>{ticket.priority}</Text></Box>
        {ticket.branch ? (
          <Box><Text color="gray">Branch   </Text><Text color="magenta">{ticket.branch}</Text>{ticket.worktree_path ? <Text color="gray"> (wt)</Text> : null}</Box>
        ) : null}
        <Box><Text color="gray">Worktree </Text>{ticket.use_worktree ? <Text color="green">on</Text> : <Text color="gray">off</Text>}</Box>
        {ticket.phase ? (
          <Box><Text color="gray">Phase    </Text><Text color="cyan">{ticket.phase}</Text></Box>
        ) : null}
        {ticket.blocked_by.length > 0 ? (
          <Box><Text color="gray">Blocked  </Text><Text>{ticket.blocked_by.join(", ")}</Text></Box>
        ) : null}
        {ticket.token_usage ? (
          <Box>
            <Text color="gray">Tokens   </Text>
            <Text color="cyan">{formatTokens(ticket.token_usage.input_tokens ?? ticket.token_usage.inputTokens ?? 0)}</Text>
            <Text color="gray"> in </Text>
            <Text color="cyan">{formatTokens(ticket.token_usage.output_tokens ?? ticket.token_usage.outputTokens ?? 0)}</Text>
            <Text color="gray"> out</Text>
          </Box>
        ) : null}
        <Box><Text color="gray">ID       </Text><Text color="gray">{ticket.id}</Text></Box>
      </Box>
      {ticket.description ? (
        <Box marginTop={1} flexDirection="column">
          <Text color="gray" bold>Description</Text>
          <Text>{ticket.description}</Text>
        </Box>
      ) : null}
      {ticket.task?.last_urls ? (
        <Box marginTop={1} flexDirection="column">
          <Text color="gray" bold>URLs</Text>
          {Object.entries(ticket.task.last_urls).map(([key, url]) => (
            <Text key={key}>{key}: {url}</Text>
          ))}
        </Box>
      ) : null}
    </Box>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

const TILE_LABELS: Record<TileType, string> = {
  chat: "Chat",
  terminal: "Term",
  supervisor: "Supervisor",
  ssh: "SSH",
  info: "Info",
  log: "Log",
  artifacts: "Artifacts",
  diff: "Diff",
};

// --- Main component ---

type Props = {
  node: TileNode;
  availCols: number;
  availRows: number;
  focusedId: string | null;
  active?: boolean;
  rawFocused?: boolean;
  sessions: Map<string, PtySession>;
  // Ticket context for content tiles
  ticket: Ticket;
  projectId: string;
  columns: Column[];
  // Content tile components (passed as render props to avoid circular deps)
  renderContentTile?: (tileType: TileType, innerCols: number, innerRows: number, focused: boolean) => React.ReactNode;
};

export function TicketTileTree({
  node, availCols, availRows, focusedId, active = false, rawFocused = false,
  sessions, ticket, projectId, columns, renderContentTile,
}: Props) {
  if (node.type === "leaf") {
    const isFocused = node.id === focusedId;
    const isRawFocused = isFocused && rawFocused;
    const isPty = PTY_TILE_TYPES.has(node.tileType);
    const session = isPty ? sessions.get(node.id) : null;
    const innerCols = Math.max(10, availCols - 2);
    const innerRows = Math.max(3, availRows - 3);

    if (isPty && session && !(session as any)._dead) {
      session.resize(innerCols, innerRows);
    }

    const showFocused = isFocused && active;
    const label = TILE_LABELS[node.tileType] ?? node.tileType;

    let content: React.ReactNode;
    if (isPty) {
      const lines = session ? session.readBuffer(innerCols, innerRows) : [];
      content = lines.length > 0
        ? <TerminalView lines={lines} cols={innerCols} rows={innerRows} />
        : <Text color="gray"> starting...</Text>;
    } else if (node.tileType === "info") {
      content = <InfoTile ticket={ticket} columns={columns} innerCols={innerCols} />;
    } else if (renderContentTile) {
      content = renderContentTile(node.tileType, innerCols, innerRows, showFocused);
    } else {
      content = <Text color="gray"> {label}</Text>;
    }

    return (
      <Panel title={label} focused={showFocused} rawFocused={isRawFocused} flexGrow={1}>
        {content}
      </Panel>
    );
  }

  // Split node — identical to TileTree
  const isH = node.direction === "horizontal";
  const aSize = isH
    ? Math.floor(availCols * node.ratio)
    : Math.floor(availRows * node.ratio);
  const bSize = isH ? availCols - aSize : availRows - aSize;

  return (
    <Box flexDirection={isH ? "row" : "column"} flexGrow={1}>
      <Box
        width={isH ? aSize : undefined}
        height={!isH ? aSize : undefined}
        flexGrow={0}
        flexShrink={0}
        flexDirection="column"
      >
        <TicketTileTree
          node={node.a}
          availCols={isH ? aSize : availCols}
          availRows={isH ? availRows : aSize}
          focusedId={focusedId}
          active={active}
          rawFocused={rawFocused}
          sessions={sessions}
          ticket={ticket}
          projectId={projectId}
          columns={columns}
          renderContentTile={renderContentTile}
        />
      </Box>
      <Box
        width={isH ? bSize : undefined}
        height={!isH ? bSize : undefined}
        flexGrow={0}
        flexShrink={0}
        flexDirection="column"
      >
        <TicketTileTree
          node={node.b}
          availCols={isH ? bSize : availCols}
          availRows={isH ? availRows : bSize}
          focusedId={focusedId}
          active={active}
          rawFocused={rawFocused}
          sessions={sessions}
          ticket={ticket}
          projectId={projectId}
          columns={columns}
          renderContentTile={renderContentTile}
        />
      </Box>
    </Box>
  );
}
