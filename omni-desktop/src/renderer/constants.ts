import type { ITerminalInitOnlyOptions, ITerminalOptions } from '@xterm/xterm';

/**
 * The interval in milliseconds to poll the status of a process.
 */
export const STATUS_POLL_INTERVAL_MS = 1_000;

export const TERMINAL_FONT = 'JetBrainsMonoNerdFont';
export const TERMINAL_FONT_SIZE = 12;

export const DEFAULT_XTERM_OPTIONS: ITerminalOptions & ITerminalInitOnlyOptions = {
  cursorBlink: false,
  cursorStyle: 'block',
  fontSize: TERMINAL_FONT_SIZE,
  fontFamily: TERMINAL_FONT,
  scrollback: 5_000,
  allowTransparency: true,
};
