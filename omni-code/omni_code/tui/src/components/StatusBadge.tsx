import React from "react";
import { Text } from "ink";
import { useTheme } from "../ThemeContext.js";
import type { Theme } from "../theme.js";

function phaseColor(phase: string, t: Theme): string {
  switch (phase) {
    case "running": case "continuing": return t.phaseRunning;
    case "provisioning": case "connecting": case "session_creating": case "retrying": return t.phaseWaiting;
    case "awaiting_input": return t.phaseInput;
    case "error": return t.phaseError;
    case "completed": case "idle": return t.phaseIdle;
    case "ready": return t.phaseReady;
    default: return t.fgSubtle;
  }
}

function priorityColor(priority: string, t: Theme): string {
  switch (priority) {
    case "low": return t.priorityLow;
    case "medium": return t.priorityMedium;
    case "high": return t.priorityHigh;
    case "critical": return t.priorityCritical;
    default: return t.fgSubtle;
  }
}

export function PhaseBadge({ phase }: { phase: string | undefined }) {
  const t = useTheme();
  if (!phase) return null;
  return <Text color={phaseColor(phase, t)}>[{phase}]</Text>;
}

export function PriorityBadge({ priority }: { priority: string }) {
  const t = useTheme();
  return <Text color={priorityColor(priority, t)}>{priority}</Text>;
}

export function TaskStatusBadge({ status }: { status: { type: string } | undefined }) {
  const t = useTheme();
  if (!status) return null;
  return <Text color={phaseColor(status.type, t)}>[{status.type}]</Text>;
}
