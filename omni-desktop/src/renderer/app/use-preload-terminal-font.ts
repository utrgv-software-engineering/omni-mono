import { TERMINAL_FONT, TERMINAL_FONT_SIZE } from '@omniagents/chat-ui/terminal/xterm-options';
import { useEffect } from 'react';

export const usePreloadTerminalFont = () => {
  useEffect(() => {
    document.fonts.load(`${TERMINAL_FONT_SIZE}px ${TERMINAL_FONT}`);
  });
};
