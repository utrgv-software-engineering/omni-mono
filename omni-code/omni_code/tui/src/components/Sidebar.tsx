import React from "react";
import { Box, Text } from "ink";
import type { Project } from "../rpc.js";

export const SIDEBAR_WIDTH = 30;

type Mode = "fleet" | "chat" | "work" | "settings" | "containers";

const MODES: { key: string; mode: Mode; short: string }[] = [
  { key: "1", mode: "chat", short: "Chat" },
  { key: "2", mode: "work", short: "Work" },
  { key: "3", mode: "fleet", short: "Fleet" },
  { key: "4", mode: "settings", short: "Set" },
  { key: "5", mode: "containers", short: "Dock" },
];

function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + "…";
}

type Props = {
  mode: Mode;
  // Fleet
  projects: Project[];
  projectIndex: number;
  selectedProject: Project | null;
  autoDispatchMap: Record<string, boolean>;
  // Chat/Work
  chatPaneOpen: boolean;
  workPaneOpen: boolean;
  // Status
  status: string | null;
};

export function Sidebar({
  mode, projects, projectIndex, selectedProject,
  autoDispatchMap, chatPaneOpen, workPaneOpen, status,
}: Props) {
  const w = SIDEBAR_WIDTH - 2;

  return (
    <Box
      flexDirection="column"
      width={SIDEBAR_WIDTH}
      borderStyle="single"
      borderRight={true}
      borderLeft={false}
      borderTop={false}
      borderBottom={false}
    >
      {/* Mode tabs */}
      <Box flexWrap="wrap">
        {MODES.map((m, i) => (
          <React.Fragment key={m.mode}>
            {i > 0 ? <Text> </Text> : null}
            <Text
              color={mode === m.mode ? "cyan" : "gray"}
              bold={mode === m.mode}
              dimColor={mode !== m.mode}
            >
              {m.key}{mode === m.mode ? m.short : ""}
            </Text>
          </React.Fragment>
        ))}
      </Box>

      {/* Fleet mode: project list */}
      {mode === "fleet" ? (
        <Box flexDirection="column" marginTop={1} flexGrow={1}>
          <Text color="gray" bold>Projects</Text>
          {projects.length === 0 ? (
            <Box flexDirection="column">
              <Text color="gray">  no projects</Text>
              <Text color="gray">  a to add</Text>
            </Box>
          ) : (
            projects.map((p, i) => {
              const isCursor = i === projectIndex && !selectedProject;
              const isCurrent = selectedProject?.id === p.id;
              const marker = isCursor ? ">" : isCurrent ? "●" : " ";
              return (
                <Text
                  key={p.id}
                  color={isCursor ? "cyan" : isCurrent ? "white" : undefined}
                  bold={isCurrent}
                >
                  {marker} {truncate(p.label, w - 8)}
                  <Text color="gray"> {p.ticket_count}</Text>
                  {p.active_count > 0 ? <Text color="green"> ●</Text> : null}
                  {autoDispatchMap[p.id] ? <Text color="magenta"> A</Text> : null}
                </Text>
              );
            })
          )}

          {!selectedProject ? (
            <Box marginTop={1}>
              <Text color="gray">
                j/k a:add d:del
              </Text>
            </Box>
          ) : null}
        </Box>
      ) : null}

      {/* Chat mode */}
      {mode === "chat" ? (
        <Box flexDirection="column" marginTop={1} flexGrow={1}>
          <Text bold>Chat</Text>
          <Text color="gray">omni coding agent</Text>
          <Box marginTop={1}>
            {chatPaneOpen ? (
              <Text color="green">● active</Text>
            ) : (
              <Text color="gray">○ not running</Text>
            )}
          </Box>
          <Box marginTop={1} flexDirection="column">
            <Text color="gray">enter open</Text>
            {chatPaneOpen ? <Text color="gray">x     close</Text> : null}
          </Box>
        </Box>
      ) : null}

      {/* Work mode */}
      {mode === "work" ? (
        <Box flexDirection="column" marginTop={1} flexGrow={1}>
          <Text bold>Work</Text>
          <Text color="gray">workspace sandbox</Text>
          <Box marginTop={1}>
            {workPaneOpen ? (
              <Text color="green">● active</Text>
            ) : (
              <Text color="gray">○ not running</Text>
            )}
          </Box>
          <Box marginTop={1} flexDirection="column">
            <Text color="gray">enter open</Text>
            {workPaneOpen ? <Text color="gray">x     close</Text> : null}
          </Box>
        </Box>
      ) : null}

      {/* Settings mode */}
      {mode === "settings" ? (
        <Box flexDirection="column" marginTop={1} flexGrow={1}>
          <Text bold>Settings</Text>
        </Box>
      ) : null}

      {/* Containers mode */}
      {mode === "containers" ? (
        <Box flexDirection="column" marginTop={1} flexGrow={1}>
          <Text bold>Containers</Text>
        </Box>
      ) : null}

      {/* Status at bottom */}
      {status ? (
        <Text color="yellow" wrap="truncate">
          {truncate(status, w)}
        </Text>
      ) : null}
    </Box>
  );
}
