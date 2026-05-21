import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useWakeup } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

const MESSAGE_TRUNCATE = 50;

function shortMessage(message: string, max: number): string {
  const oneLine = message.replace(/\s+/g, " ").trim();
  if (oneLine.length <= max) return oneLine;
  return oneLine.slice(0, max - 1) + "…";
}

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes - hours * 60;
  return rem === 0 ? `${hours}h` : `${hours}h${rem}m`;
}

function formatTimeRemaining(unixTimestamp: number): string {
  const remaining = Math.max(0, unixTimestamp - Date.now() / 1000);
  return formatInterval(remaining);
}

// Compact single-line docked panel for the schedule_wakeup tick loop.
// Renders nothing when no schedule is active — server pushes
// snapshot=null on cancel/exhaustion.
//
// Active recurring:  ⠋ wakeup · check the build · every 5m · 3 fires
// Active one-shot:   ⠋ wakeup · check the build · in 30s
// With end_after:    ⠋ wakeup · check the build · every 5m · 3 left
// With end_at:       ⠋ wakeup · check the build · every 5m · until 2h
// Cancelled:         ✗ wakeup · check the build · cancelled
export const Wakeup = React.memo(() => {
  const wakeup = useWakeup();
  if (!wakeup) return null;

  const isActive = wakeup.status === "active";
  const glyphColor = isActive ? TokyoNightTheme.info : TokyoNightTheme.error;

  const parts: string[] = [];
  if (wakeup.recurring) {
    parts.push(`every ${formatInterval(wakeup.interval_seconds)}`);
    if (wakeup.end_after !== null && wakeup.end_after !== undefined) {
      parts.push(`${wakeup.end_after} left`);
    } else if (wakeup.end_at !== null && wakeup.end_at !== undefined) {
      parts.push(`until ${formatTimeRemaining(wakeup.end_at)}`);
    } else if (wakeup.fires > 0) {
      parts.push(
        `${wakeup.fires} fire${wakeup.fires === 1 ? "" : "s"}`,
      );
    }
  } else {
    if (wakeup.end_at !== null && wakeup.end_at !== undefined) {
      parts.push(`in ${formatTimeRemaining(wakeup.end_at)}`);
    } else {
      parts.push(`in ${formatInterval(wakeup.interval_seconds)}`);
    }
  }

  return (
    <Box>
      <Text color={glyphColor}>
        {isActive ? <Spinner type="dots" /> : "✗"}
      </Text>
      <Text color={TokyoNightTheme.defaultText}> wakeup </Text>
      <Text color={TokyoNightTheme.toolResult}>· </Text>
      <Text color={TokyoNightTheme.inputText}>
        {shortMessage(wakeup.message, MESSAGE_TRUNCATE)}
      </Text>
      {isActive ? (
        parts.map((part, idx) => (
          <React.Fragment key={idx}>
            <Text color={TokyoNightTheme.toolResult}> · </Text>
            <Text color={TokyoNightTheme.defaultText}>{part}</Text>
          </React.Fragment>
        ))
      ) : (
        <>
          <Text color={TokyoNightTheme.toolResult}> · </Text>
          <Text color={TokyoNightTheme.defaultText}>{wakeup.status}</Text>
        </>
      )}
    </Box>
  );
});
