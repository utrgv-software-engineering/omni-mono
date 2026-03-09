import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Text, useInput, type Key } from "ink";
import type { Project, Ticket } from "../rpc.js";
import { api } from "../rpc.js";
import { KeyBar, type Hint } from "../components/KeyHint.js";
import { TaskStatusBadge } from "../components/StatusBadge.js";
import { TextInput } from "../components/TextInput.js";
import { HelpOverlay } from "../components/HelpOverlay.js";
import { ConfirmDialog } from "../components/ConfirmDialog.js";
import { FilterInput, fuzzyMatch } from "../components/FilterInput.js";
import { useTheme } from "../ThemeContext.js";

type Props = {
  onSelectProject: (project: Project) => void;
  onQuit: () => void;
  onInputActive?: (active: boolean) => void;
};

function clamp(value: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(value, max - 1));
}

type ActiveTicket = Ticket & { project_label: string };

const HINTS: Hint[] = [
  { key: "j/k", label: "navigate" },
  { key: "enter", label: "open board" },
  { key: "tab", label: "switch pane" },
  { key: "a", label: "add project" },
  { key: "d", label: "delete project" },
  { key: "/", label: "filter" },
  { key: "1-9", label: "jump to active" },
  { key: "r", label: "refresh" },
  { key: "?", label: "help" },
  { key: "q", label: "quit" },
];

export function Dashboard({ onSelectProject, onQuit, onInputActive }: Props) {
  const t = useTheme();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [activeTickets, setActiveTickets] = useState<ActiveTicket[]>([]);
  const [pane, setPane] = useState<"projects" | "active">("projects");
  const [activeIndex, setActiveIndex] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [addPath, setAddPath] = useState("");
  const [showHelp, setShowHelp] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<Project | null>(null);
  const [filterQuery, setFilterQuery] = useState("");
  const [filtering, setFiltering] = useState(false);
  const [autoDispatchMap, setAutoDispatchMap] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    try {
      const projectList = await api.listProjects();
      setProjects(projectList);

      const allActive: ActiveTicket[] = [];
      for (const project of projectList) {
        const tickets = await api.listTickets(project.id);
        for (const ticket of tickets) {
          if (ticket.supervisor_session_id || (ticket.task && ticket.task.status?.type === "running")) {
            allActive.push({ ...ticket, project_label: project.label });
          }
        }
      }
      allActive.sort((a, b) => b.updated_at - a.updated_at);
      setActiveTickets(allActive);

      // Fetch auto-dispatch status for each project
      const adMap: Record<string, boolean> = {};
      for (const project of projectList) {
        try {
          const result = await api.getAutoDispatch(project.id);
          adMap[project.id] = result.enabled;
        } catch {
          adMap[project.id] = false;
        }
      }
      setAutoDispatchMap(adMap);

    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
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

  const filteredProjects = useMemo(
    () => projects.filter((p) => fuzzyMatch(p.label, filterQuery)),
    [projects, filterQuery],
  );

  useInput((input: string, key: Key) => {
    if (showHelp || adding || confirmDelete || filtering) return;

    if (input === "?") {
      setShowHelp(true);
      return;
    }
    if (input === "/") {
      setFiltering(true);
      setFilterQuery("");
      onInputActive?.(true);
      return;
    }
    if (input === "q" || key.escape) {
      onQuit();
      return;
    }
    if (input === "r") {
      refresh();
      setStatus("Refreshed");
      return;
    }
    if (key.tab) {
      setPane((prev) => (prev === "projects" ? "active" : "projects"));
      return;
    }

    if (pane === "projects") {
      if (key.upArrow || input === "k") {
        setSelectedIndex((v) => clamp(v - 1, filteredProjects.length));
        return;
      }
      if (key.downArrow || input === "j") {
        setSelectedIndex((v) => clamp(v + 1, filteredProjects.length));
        return;
      }
      if (key.return) {
        const project = filteredProjects[selectedIndex];
        if (project) onSelectProject(project);
        return;
      }
      if (input === "a") {
        setAdding(true);
        setAddPath("");
        onInputActive?.(true);
        return;
      }
      if (input === "d") {
        const project = filteredProjects[selectedIndex];
        if (project) {
          setConfirmDelete(project);
        }
        return;
      }
    }

    if (pane === "active") {
      if (key.upArrow || input === "k") {
        setActiveIndex((v) => clamp(v - 1, activeTickets.length));
        return;
      }
      if (key.downArrow || input === "j") {
        setActiveIndex((v) => clamp(v + 1, activeTickets.length));
        return;
      }
    }

    // Number keys jump to active ticket's project
    const digit = parseInt(input, 10);
    if (!isNaN(digit) && digit >= 1 && digit <= activeTickets.length) {
      const ticket = activeTickets[digit - 1];
      if (ticket) {
        const project = projects.find((p) => p.id === ticket.project_id);
        if (project) onSelectProject(project);
      }
    }
  });

  const handleAddSubmit = useCallback(
    async (path: string) => {
      const trimmed = path.trim();
      if (!trimmed) {
        setAdding(false);
        onInputActive?.(false);
        return;
      }
      try {
        const parts = trimmed.split("/");
        const label = parts[parts.length - 1] || trimmed;
        await api.addProject(label, trimmed);
        setStatus(`Added project ${label}`);
        setAdding(false);
        onInputActive?.(false);
        refresh();
      } catch (err) {
        setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
        setAdding(false);
        onInputActive?.(false);
      }
    },
    [refresh],
  );

  const handleConfirmDelete = useCallback(() => {
    if (!confirmDelete) return;
    const project = confirmDelete;
    setConfirmDelete(null);
    api.removeProject(project.id).then(() => {
      setStatus(`Removed ${project.label}`);
      refresh();
    }).catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
  }, [confirmDelete, refresh]);

  if (showHelp) {
    return (
      <Box flexDirection="column" paddingX={1}>
        <HelpOverlay title="Dashboard" hints={HINTS} onClose={() => setShowHelp(false)} />
      </Box>
    );
  }

  return (
    <Box flexDirection="column" paddingX={1}>
      <Box marginBottom={1}>
        <Text color={t.fg} bold>Omni Projects</Text>
        <Text color={t.fgSubtle}> — dashboard</Text>
      </Box>

      <Box>
        {/* Left pane: projects */}
        <Box width="40%" flexDirection="column" marginRight={2}>
          <Text color={pane === "projects" ? t.accent : t.fgSubtle} bold>
            Projects
          </Text>
          {filteredProjects.length === 0 ? (
            <Text color={t.fgSubtle}>{projects.length === 0 ? "No projects. Press a to add one." : "No matches."}</Text>
          ) : (
            filteredProjects.map((project, index) => {
              const isSelected = pane === "projects" && index === selectedIndex;
              const activeLabel =
                project.active_count > 0 ? (
                  <Text color={t.success}> {project.active_count} active</Text>
                ) : null;
              return (
                <Text key={project.id} color={isSelected ? t.accent : undefined}>
                  {isSelected ? "> " : "  "}
                  {project.label} ({project.ticket_count}){activeLabel}
                  {autoDispatchMap[project.id] ? <Text color={t.accentSecondary}> [A]</Text> : null}
                </Text>
              );
            })
          )}
        </Box>

        {/* Right pane: active work */}
        <Box width="60%" flexDirection="column">
          <Text color={pane === "active" ? t.accent : t.fgSubtle} bold>
            Active Work
          </Text>
          {activeTickets.length === 0 ? (
            <Text color={t.fgSubtle}>No active tasks.</Text>
          ) : (
            activeTickets.map((ticket, index) => {
              const isSelected = pane === "active" && index === activeIndex;
              return (
                <Box key={ticket.id}>
                  <Text color={isSelected ? t.accent : undefined}>
                    {isSelected ? "> " : "  "}
                    <Text color={t.warning}>{index + 1}</Text> {ticket.title}
                  </Text>
                  <Text> </Text>
                  <TaskStatusBadge status={ticket.task?.status} />
                  <Text> </Text>
                  <Text color={t.fgSubtle}>{ticket.column_id}</Text>
                  <Text> </Text>
                  <Text color={t.fgSubtle}>{ticket.project_label}</Text>
                </Box>
              );
            })
          )}
        </Box>
      </Box>

      {adding ? (
        <Box marginTop={1}>
          <TextInput
            value={addPath}
            onChange={setAddPath}
            onSubmit={handleAddSubmit}
            onCancel={() => { setAdding(false); onInputActive?.(false); }}
            prompt="Workspace path:"
            placeholder="/path/to/repo"
          />
        </Box>
      ) : null}

      {confirmDelete ? (
        <Box marginTop={1}>
          <ConfirmDialog
            message={`Delete project "${confirmDelete.label}"?`}
            onConfirm={handleConfirmDelete}
            onCancel={() => setConfirmDelete(null)}
          />
        </Box>
      ) : null}

      {filtering ? (
        <Box marginTop={1}>
          <FilterInput
            value={filterQuery}
            onChange={setFilterQuery}
            onClose={() => { setFiltering(false); onInputActive?.(false); }}
          />
        </Box>
      ) : null}

      <Box marginTop={1}>
        <KeyBar hints={HINTS} />
      </Box>

      {status ? (
        <Box>
          <Text color={t.warning}>{status}</Text>
        </Box>
      ) : null}
    </Box>
  );
}
