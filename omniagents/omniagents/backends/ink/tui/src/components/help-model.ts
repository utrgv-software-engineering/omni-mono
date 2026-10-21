// The one place every key is written down. The `?` overlay renders this;
// nothing else should hard-code a binding description. When a key moves,
// this list is the thing that has to change with it.

import type { Screen } from "./StatusBar.js";

export type HelpRow = { keys: string; label: string };
export type HelpSection = { title: string; rows: HelpRow[] };

const GLOBAL: HelpSection = {
  title: "Everywhere",
  rows: [
    { keys: "tab / shift+tab", label: "next / previous screen" },
    { keys: "alt+1..2", label: "chat · agents" },
    { keys: "ctrl+p", label: "sessions: switch, search, fork" },
    { keys: "ctrl+k", label: "command palette" },
    { keys: "ctrl+g", label: "git panel" },
    { keys: "ctrl+b", label: "background jobs" },
    { keys: "ctrl+n", label: "notifications" },
    { keys: "ctrl+o", label: "transcript: activity beats ↔ every tool call" },
    { keys: "shift+↑ / shift+↓", label: "scroll the transcript" },
    { keys: "esc", label: "close a panel · deny an approval · stop the run" },
    { keys: "ctrl+c ctrl+c", label: "quit (first press stops the run)" },
    { keys: "?", label: "this overlay (on an empty composer)" },
  ],
};

const COMPOSER: HelpSection = {
  title: "Composer",
  rows: [
    { keys: "enter", label: "send (queues while a run is active)" },
    { keys: "shift+enter", label: "new line (ctrl+enter, alt+enter too)" },
    { keys: "/", label: "commands: ↑↓ pick, enter run, tab complete" },
    {
      keys: "/theme",
      label: "color theme: Omarchy's, yours, or a bundled one",
    },
    {
      keys: "/goal · /loop",
      label: "alone: open the panel · with text: start one",
    },
    { keys: "/jobs /tasks /queue …", label: "every panel opens from its verb" },
    { keys: "@", label: "files: ↑↓ pick, tab or enter insert" },
    { keys: "↑ / ↓", label: "history at the top / bottom of the text" },
    { keys: "home / end", label: "line start / end" },
    { keys: "ctrl+d", label: "delete forward" },
  ],
};

const DECISIONS: HelpSection = {
  title: "Approvals and questions",
  rows: [
    { keys: "1 2 3", label: "approve once · always · deny" },
    { keys: "↑↓ enter", label: "pick an answer" },
    { keys: "esc", label: "deny" },
    {
      keys: "type",
      label: "a yellow question banner is answered by your next message",
    },
  ],
};

const SCREENS: Record<Screen, HelpSection> = {
  chat: { title: "Chat", rows: [] },
  agents: {
    title: "Agents",
    rows: [
      { keys: "↑↓", label: "select a worker" },
      { keys: "enter", label: "open its transcript" },
    ],
  },
};

export function helpSections(screen: Screen): HelpSection[] {
  const sections = [GLOBAL, COMPOSER, DECISIONS];
  const current = SCREENS[screen];
  if (current.rows.length) sections.push(current);
  return sections;
}
