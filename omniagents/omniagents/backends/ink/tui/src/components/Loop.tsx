import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useLoops } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

const PROMPT_TRUNCATE = 50;

function shortPrompt(prompt: string): string {
  const oneLine = prompt.replace(/\s+/g, " ").trim();
  if (oneLine.length <= PROMPT_TRUNCATE) return oneLine;
  return oneLine.slice(0, PROMPT_TRUNCATE - 1) + "…";
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

export const Loop = React.memo(() => {
  const loops = useLoops().filter((loop) => loop.status === "active");
  if (loops.length === 0) return null;

  const next = [...loops].sort(
    (a, b) =>
      (a.next_fire_at ?? Number.MAX_SAFE_INTEGER) -
      (b.next_fire_at ?? Number.MAX_SAFE_INTEGER),
  )[0];
  const cadence =
    next.mode === "dynamic"
      ? "dynamic"
      : next.interval_seconds
        ? `every ${formatInterval(next.interval_seconds)}`
        : `cron ${next.cron}`;

  return (
    <Box>
      <Text color={TokyoNightTheme.info}>
        <Spinner type="dots" />
      </Text>
      <Text color={TokyoNightTheme.defaultText}> loop </Text>
      <Text color={TokyoNightTheme.toolResult}>· </Text>
      <Text color={TokyoNightTheme.inputText}>{shortPrompt(next.prompt)}</Text>
      <Text color={TokyoNightTheme.toolResult}> · </Text>
      <Text color={TokyoNightTheme.defaultText}>{cadence}</Text>
      {next.next_fire_at ? (
        <>
          <Text color={TokyoNightTheme.toolResult}> · </Text>
          <Text color={TokyoNightTheme.defaultText}>
            in {formatTimeRemaining(next.next_fire_at)}
          </Text>
        </>
      ) : null}
      <Text color={TokyoNightTheme.toolResult}> · </Text>
      <Text color={TokyoNightTheme.defaultText}>id {next.id}</Text>
      {loops.length > 1 ? (
        <>
          <Text color={TokyoNightTheme.toolResult}> · </Text>
          <Text color={TokyoNightTheme.defaultText}>{loops.length} loops</Text>
        </>
      ) : null}
    </Box>
  );
});
