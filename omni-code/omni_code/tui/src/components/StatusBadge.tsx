import React from "react";
import { Text } from "ink";

const PHASE_COLORS: Record<string, string> = {
  running: "green",
  continuing: "green",
  provisioning: "yellow",
  connecting: "yellow",
  session_creating: "yellow",
  retrying: "yellow",
  awaiting_input: "blue",
  error: "red",
  completed: "gray",
  idle: "gray",
  ready: "cyan",
};

const PRIORITY_COLORS: Record<string, string> = {
  low: "gray",
  medium: "blue",
  high: "yellow",
  critical: "red",
};

export function PhaseBadge({ phase }: { phase: string | undefined }) {
  if (!phase) return null;
  const color = PHASE_COLORS[phase] ?? "gray";
  return <Text color={color}>[{phase}]</Text>;
}

export function PriorityBadge({ priority }: { priority: string }) {
  const color = PRIORITY_COLORS[priority] ?? "gray";
  return <Text color={color}>{priority}</Text>;
}

export function TaskStatusBadge({ status }: { status: { type: string } | undefined }) {
  if (!status) return null;
  const color = PHASE_COLORS[status.type] ?? "gray";
  return <Text color={color}>[{status.type}]</Text>;
}
