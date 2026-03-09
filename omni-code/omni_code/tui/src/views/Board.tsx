import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Text, useInput, useStdout, type Key } from "ink";
import type { Column, Project, Ticket } from "../rpc.js";
import { api } from "../rpc.js";
import { Panel } from "../components/Panel.js";
import { PhaseBadge, PriorityBadge, TaskStatusBadge } from "../components/StatusBadge.js";
import { TextInput } from "../components/TextInput.js";
import { HelpOverlay } from "../components/HelpOverlay.js";
import { ConfirmDialog } from "../components/ConfirmDialog.js";
import { FilterInput, fuzzyMatch } from "../components/FilterInput.js";
import { Terminal } from "../components/Terminal.js";
import type { Hint } from "../components/KeyHint.js";
import { useTheme } from "../ThemeContext.js";

type Props = {
  project: Project;
  onSelectTicket: (ticket: Ticket) => void;
  onBack: () => void;
  onQuit: () => void;
  active?: boolean;
  onInputActive?: (active: boolean) => void;
  onHints?: (hints: Hint[]) => void;
  onStatus?: (status: string | null) => void;
};

function clamp(value: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(value, max - 1));
}

type ColumnPicker = {
  ticket: Ticket;
  columns: Column[];
  selectedIndex: number;
};

const HELP_HINTS: Hint[] = [
  { key: "h/l", label: "switch column" },
  { key: "j/k", label: "navigate tickets" },
  { key: "enter", label: "open ticket detail" },
  { key: "a", label: "add new ticket" },
  { key: "m", label: "move ticket to column" },
  { key: "s", label: "start supervisor" },
  { key: "X", label: "stop supervisor" },
  { key: "H", label: "SSH into container" },
  { key: "o", label: "launch omni agent" },
  { key: "c", label: "resume chat" },
  { key: "d", label: "delete ticket" },
  { key: "D", label: "toggle auto-dispatch" },
  { key: "/", label: "filter tickets" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "esc", label: "back" },
];

const BAR_HINTS: Hint[] = [
  { key: "h/l", label: "column" },
  { key: "j/k", label: "ticket" },
  { key: "enter", label: "detail" },
  { key: "a", label: "add" },
  { key: "m", label: "move" },
  { key: "s", label: "start" },
  { key: "o", label: "omni" },
  { key: "d", label: "delete" },
  { key: "/", label: "filter" },
  { key: "?", label: "help" },
  { key: "esc", label: "back" },
];

export function Board({ project, onSelectTicket, onBack, onQuit, active = true, onInputActive, onHints, onStatus }: Props) {
  const t = useTheme();
  const { stdout } = useStdout();
  const rows = stdout?.rows ?? 24;
  const cols = stdout?.columns ?? 80;

  const [columns, setColumns] = useState<Column[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [colIndex, setColIndex] = useState(0);
  const [rowIndex, setRowIndex] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [adding, setAdding] = useState<"title" | "description" | false>(false);
  const [addTitle, setAddTitle] = useState("");
  const [addDescription, setAddDescription] = useState("");
  const [columnPicker, setColumnPicker] = useState<ColumnPicker | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [confirmAction, setConfirmAction] = useState<{ message: string; action: () => void } | null>(null);
  const [filterQuery, setFilterQuery] = useState("");
  const [filtering, setFiltering] = useState(false);
  const [autoDispatch, setAutoDispatch] = useState(false);

  // Embedded terminal state — replaces the board content when active
  const [activeTerminal, setActiveTerminal] = useState<{
    command: string[];
    cwd?: string;
    label: string;
  } | null>(null);
  const [termFocused, setTermFocused] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [pipelineResult, ticketList] = await Promise.all([
        api.getPipeline(project.id),
        api.listTickets(project.id),
      ]);
      setColumns(pipelineResult.columns);
      setTickets(ticketList);
      api.getAutoDispatch(project.id).then((r) => setAutoDispatch(r.enabled)).catch(() => {});
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [project.id]);

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

  // Report status/hints to parent for app-level StatusBar
  useEffect(() => { onStatus?.(status ?? null); }, [status]);
  useEffect(() => {
    onHints?.([
      ...BAR_HINTS,
      ...(autoDispatch ? [{ key: "D", label: "auto\u2713" }] : [{ key: "D", label: "auto" }]),
      ...(filterQuery ? [{ key: "filter", label: filterQuery }] : []),
    ]);
  }, [autoDispatch, filterQuery]);

  const ticketsByColumn = useMemo(() => {
    const map = new Map<string, Ticket[]>();
    for (const col of columns) {
      map.set(col.id, []);
    }
    for (const ticket of tickets) {
      if (!fuzzyMatch(ticket.title, filterQuery)) continue;
      const bucket = map.get(ticket.column_id);
      if (bucket) bucket.push(ticket);
    }
    return map;
  }, [columns, tickets, filterQuery]);

  const currentColumnTickets = useMemo(() => {
    const col = columns[colIndex];
    return col ? ticketsByColumn.get(col.id) ?? [] : [];
  }, [columns, colIndex, ticketsByColumn]);

  const selectedTicket = currentColumnTickets[rowIndex] ?? null;

  useEffect(() => {
    setRowIndex(0);
  }, [colIndex]);

  useInput((input: string, key: Key) => {
    if (!active) return;

    // When terminal is showing but not focused, Esc closes it
    if (activeTerminal && !termFocused) {
      if (key.escape) {
        setActiveTerminal(null);
        return;
      }
      // Any other key re-focuses the terminal
      setTermFocused(true);
      return;
    }

    if (adding || showHelp || confirmAction || filtering) return;

    // Column picker mode
    if (columnPicker) {
      if (key.escape) { setColumnPicker(null); return; }
      if (key.upArrow || input === "k") {
        setColumnPicker((prev) => prev ? { ...prev, selectedIndex: clamp(prev.selectedIndex - 1, prev.columns.length) } : null);
        return;
      }
      if (key.downArrow || input === "j") {
        setColumnPicker((prev) => prev ? { ...prev, selectedIndex: clamp(prev.selectedIndex + 1, prev.columns.length) } : null);
        return;
      }
      if (key.return) {
        const targetCol = columnPicker.columns[columnPicker.selectedIndex];
        if (targetCol && targetCol.id !== columnPicker.ticket.column_id) {
          api.moveTicket(columnPicker.ticket.id, targetCol.id, project.id)
            .then(() => { setStatus(`Moved to ${targetCol.label}`); setColumnPicker(null); refresh(); })
            .catch((err) => { setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`); setColumnPicker(null); });
        } else { setColumnPicker(null); }
        return;
      }
      return;
    }

    if (input === "?") { setShowHelp(true); return; }
    if (input === "/") { setFiltering(true); setFilterQuery(""); onInputActive?.(true); return; }
    if (key.escape) { onBack(); return; }
    if (input === "q") { onQuit(); return; }
    if (input === "r") { refresh(); setStatus("Refreshed"); return; }

    // Ctrl+arrows are handled by App for spatial navigation — don't consume them here
    if (key.ctrl && (key.leftArrow || key.rightArrow || key.upArrow || key.downArrow)) return;

    // Navigation
    if (key.leftArrow || input === "h") { setColIndex((v) => clamp(v - 1, columns.length)); return; }
    if (key.rightArrow || input === "l") { setColIndex((v) => clamp(v + 1, columns.length)); return; }
    if (key.upArrow || input === "k") { setRowIndex((v) => clamp(v - 1, currentColumnTickets.length)); return; }
    if (key.downArrow || input === "j") { setRowIndex((v) => clamp(v + 1, currentColumnTickets.length)); return; }

    // Actions
    if (key.return && selectedTicket) { onSelectTicket(selectedTicket); return; }
    if (input === "a") { setAdding("title"); setAddTitle(""); setAddDescription(""); onInputActive?.(true); return; }
    if (input === "m" && selectedTicket) {
      setColumnPicker({ ticket: selectedTicket, columns, selectedIndex: columns.findIndex((c) => c.id === selectedTicket.column_id) });
      return;
    }
    if (input === "s" && selectedTicket) {
      setStatus("Starting supervisor (auto)...");
      api.sandboxStart(selectedTicket.id, project.id)
        .then(() => api.supervisorAutoStart(selectedTicket.id, undefined, project.id))
        .then(() => { setStatus(`Started supervisor (auto)`); refresh(); })
        .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    if (input === "X" && selectedTicket) {
      const ticket = selectedTicket;
      setConfirmAction({
        message: `Stop supervisor for "${ticket.title}"?`,
        action: () => {
          api.supervisorAutoStop(ticket.id, project.id)
            .then(() => { setStatus(`Stopped supervisor`); refresh(); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
        },
      });
      return;
    }
    if (input === "H" && selectedTicket) {
      setStatus("Resolving SSH...");
      api.terminalResolveSsh(selectedTicket.id, project.id)
        .then((r) => { setActiveTerminal({ command: r.command, cwd: r.cwd, label: r.label }); setTermFocused(true); setStatus(null); })
        .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    if (input === "o" && selectedTicket) {
      setStatus("Resolving omni...");
      api.terminalResolveOmni(selectedTicket.id, project.id)
        .then((r) => { setActiveTerminal({ command: r.command, cwd: r.cwd, label: r.label }); setTermFocused(true); setStatus(null); })
        .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    if (input === "c" && selectedTicket) {
      setStatus("Resolving chat...");
      api.terminalResolveChat(selectedTicket.id, project.id)
        .then((r) => { setActiveTerminal({ command: r.command, cwd: r.cwd, label: r.label }); setTermFocused(true); setStatus(null); })
        .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    if (input === "D") {
      const newValue = !autoDispatch;
      api.setAutoDispatch(project.id, newValue)
        .then(() => { setAutoDispatch(newValue); setStatus(`Auto-dispatch ${newValue ? "enabled" : "disabled"}`); })
        .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    if (input === "d" && selectedTicket) {
      const ticket = selectedTicket;
      setConfirmAction({
        message: `Delete ticket "${ticket.title}"?`,
        action: () => {
          api.deleteTicket(ticket.id, project.id)
            .then(() => { setStatus(`Deleted "${ticket.title}"`); refresh(); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
        },
      });
      return;
    }
  });

  const handleTitleSubmit = useCallback((title: string) => {
    const trimmed = title.trim();
    if (!trimmed) { setAdding(false); onInputActive?.(false); return; }
    setAddTitle(trimmed);
    setAdding("description");
  }, []);

  const handleDescriptionSubmit = useCallback(async (description: string) => {
    const desc = description.trim();
    try {
      const newTicket = await api.addTicket(project.id, addTitle);
      if (desc) await api.updateTicket(newTicket.id, { description: desc }, project.id);
      setStatus(`Added "${addTitle}"`);
      setAdding(false);
      onInputActive?.(false);
      refresh();
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setAdding(false);
      onInputActive?.(false);
    }
  }, [project.id, addTitle, refresh]);

  if (showHelp) {
    return (
      <Box flexDirection="column" flexGrow={1}>
        <HelpOverlay title={project.label} hints={HELP_HINTS} onClose={() => setShowHelp(false)} />
      </Box>
    );
  }

  // When an embedded terminal is active, it replaces the board content
  if (activeTerminal) {
    return (
      <Box flexDirection="column" flexGrow={1}>
        <Panel title={activeTerminal.label} focused={termFocused} flexGrow={1}>
          <Terminal
            key={activeTerminal.command.join(" ")}
            command={activeTerminal.command}
            cwd={activeTerminal.cwd}
            focused={termFocused}
            onExit={() => { setTermFocused(false); setActiveTerminal(null); setStatus("Terminal exited"); }}
            onFocusRequest={() => setTermFocused(false)}
          />
        </Panel>
        {!termFocused ? (
          <Box paddingX={1}>
            <Text color={t.fgSubtle}>Press any key to re-focus terminal · esc to close</Text>
          </Box>
        ) : null}
      </Box>
    );
  }

  // Calculate column width for horizontal layout
  const colCount = columns.length || 1;
  const colWidthPct = `${Math.floor(100 / colCount)}%` as const;

  return (
    <Box flexDirection="column" flexGrow={1}>
      {/* Board columns */}
      <Box flexDirection="row" flexGrow={1}>
        {columns.map((col, ci) => {
          const colTickets = ticketsByColumn.get(col.id) ?? [];
          const isActive = ci === colIndex;
          return (
            <Panel
              key={col.id}
              title={`${col.label} (${colTickets.length})${col.gate ? " G" : ""}`}
              focused={isActive}
              flexGrow={1}
            >
              <Box flexDirection="column">
                {colTickets.length === 0 ? (
                  <Text color={t.fgSubtle}> --</Text>
                ) : (
                  colTickets.map((ticket, ti) => {
                    const isSelected = isActive && ti === rowIndex;
                    return (
                      <Box key={ticket.id} flexDirection="column">
                        <Text color={isSelected ? t.accent : undefined} wrap="truncate">
                          {isSelected ? "\u25B8 " : "  "}
                          {ticket.title}
                        </Text>
                        <Text wrap="truncate">
                          {"  "}
                          <PriorityBadge priority={ticket.priority} />
                          {ticket.phase ? (
                            <> <PhaseBadge phase={ticket.phase} /></>
                          ) : ticket.task ? (
                            <> <TaskStatusBadge status={ticket.task.status} /></>
                          ) : null}
                        </Text>
                      </Box>
                    );
                  })
                )}
              </Box>
            </Panel>
          );
        })}
      </Box>

      {/* Column picker overlay */}
      {columnPicker ? (
        <Box flexDirection="column" paddingX={1}>
          <Text color={t.accent}>Move "{columnPicker.ticket.title}" to:</Text>
          {columnPicker.columns.map((col, i) => (
            <Text key={col.id} color={i === columnPicker.selectedIndex ? t.accent : undefined}>
              {i === columnPicker.selectedIndex ? "\u25B8 " : "  "}{col.label}
              {col.id === columnPicker.ticket.column_id ? " (current)" : ""}
            </Text>
          ))}
          <Text color={t.fgSubtle}>enter select · esc cancel</Text>
        </Box>
      ) : null}

      {/* Add ticket input */}
      {adding === "title" ? (
        <Box paddingX={1}>
          <TextInput value={addTitle} onChange={setAddTitle} onSubmit={handleTitleSubmit} onCancel={() => { setAdding(false); onInputActive?.(false); }} prompt="Title:" placeholder="ticket title" />
        </Box>
      ) : adding === "description" ? (
        <Box paddingX={1} flexDirection="column">
          <Text color={t.accent}>Title: {addTitle}</Text>
          <TextInput value={addDescription} onChange={setAddDescription} onSubmit={handleDescriptionSubmit} onCancel={() => { setAdding(false); onInputActive?.(false); }} prompt="Desc:" placeholder="optional (enter to skip)" />
        </Box>
      ) : null}

      {confirmAction ? (
        <Box paddingX={1}>
          <ConfirmDialog message={confirmAction.message} onConfirm={() => { confirmAction.action(); setConfirmAction(null); }} onCancel={() => setConfirmAction(null)} />
        </Box>
      ) : null}

      {filtering ? (
        <Box paddingX={1}>
          <FilterInput value={filterQuery} onChange={setFilterQuery} onClose={() => { setFiltering(false); onInputActive?.(false); }} />
        </Box>
      ) : null}

    </Box>
  );
}
