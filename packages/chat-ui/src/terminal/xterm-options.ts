import type { ITerminalInitOnlyOptions, ITerminalOptions } from '@xterm/xterm';

/** The terminal face; hosts that bundle it preload it (the desktop does). */
export const TERMINAL_FONT = 'JetBrainsMonoNerdFont';
export const TERMINAL_FONT_SIZE = 12;

export const XTERM_OPTIONS: ITerminalOptions & ITerminalInitOnlyOptions = {
  cursorBlink: false,
  cursorStyle: 'block',
  fontSize: TERMINAL_FONT_SIZE,
  fontFamily: `${TERMINAL_FONT}, ui-monospace, monospace`,
  scrollback: 5_000,
  allowTransparency: true,
};
