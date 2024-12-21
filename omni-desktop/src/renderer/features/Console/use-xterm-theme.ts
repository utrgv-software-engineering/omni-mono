import { useToken } from '@invoke-ai/ui-library';
import type { ITheme } from '@xterm/xterm';
import { useMemo } from 'react';

export const useXTermTheme = (): ITheme => {
  const [
    base50,
    base100,
    base950,
    invokeBlue300,
    teal300,
    invokeGreen300,
    invokeYellow300,
    invokeRed300,
    invokePurple300,
  ] = useToken('colors', [
    'base.50',
    'base.100',
    'base.950',
    'invokeBlue.300',
    'teal.300',
    'invokeGreen.300',
    'invokeYellow.300',
    'invokeRed.300',
    'invokePurple.300',
  ]);
  const theme = useMemo(() => {
    const theme: ITheme = {
      background: 'rgba(0, 0, 0, 0)',
      foreground: base100,
      black: base100,
      brightBlack: base100,
      white: base950,
      brightWhite: base950,
      cursor: base100,
      cursorAccent: base50,
      blue: invokeBlue300,
      brightBlue: invokeBlue300,
      cyan: teal300,
      brightCyan: teal300,
      green: invokeGreen300,
      brightGreen: invokeGreen300,
      yellow: invokeYellow300,
      brightYellow: invokeYellow300,
      red: invokeRed300,
      brightRed: invokeRed300,
      magenta: invokePurple300,
      brightMagenta: invokePurple300,
    };
    return theme;
  }, [
    base100,
    base50,
    base950,
    invokeBlue300,
    invokeGreen300,
    invokePurple300,
    invokeRed300,
    invokeYellow300,
    teal300,
  ]);

  return theme;
};
