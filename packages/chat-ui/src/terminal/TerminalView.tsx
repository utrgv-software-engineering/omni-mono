import '@xterm/xterm/css/xterm.css';
import './terminal.css';

import type { TerminalState } from '@omniagents/chat-ui/terminal/terminal-store';
import { useXTermTheme } from '@omniagents/chat-ui/terminal/use-xterm-theme';
import { debounce } from 'es-toolkit/compat';
import { memo, useCallback, useEffect, useRef } from 'react';
import { assert } from 'tsafe';

/** Shows one terminal's xterm, fitted to its container. */
export const TerminalView = memo(({ terminal, isActive }: { terminal: TerminalState; isActive: boolean }) => {
  const theme = useXTermTheme();
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    const parent = el?.parentElement;
    assert(el);
    assert(parent);

    terminal.xterm.options.theme = theme;

    const fit = () => {
      terminal.fitAddon.fit();
    };
    const debouncedFit = debounce(fit, 300, { leading: false, trailing: true });

    const resizeObserver = new ResizeObserver(debouncedFit);
    resizeObserver.observe(parent);

    // xterm's `open()` is documented as one-shot. On remount (e.g. toggling
    // sidecar apps) the xterm instance lives on in the atom — reparent its
    // existing root element instead of calling `open()` a second time, which
    // leaves the renderer stuck pointing at the old (detached) DOM.
    const existingRoot = terminal.xterm.element;
    if (existingRoot) {
      el.appendChild(existingRoot);
    } else {
      terminal.xterm.open(el);
    }

    // Defer to rAF so the new parent has layout, then fit + refresh so the
    // viewport paints the scrollback on the fresh DOM.
    const raf = requestAnimationFrame(() => {
      fit();
      terminal.xterm.refresh(0, terminal.xterm.rows - 1);
    });

    return () => {
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
    };
  }, [terminal.fitAddon, terminal.id, terminal.xterm, theme]);

  useEffect(() => {
    if (!isActive) {
      return;
    }
    const raf = requestAnimationFrame(() => terminal.xterm.focus());
    return () => cancelAnimationFrame(raf);
  }, [isActive, terminal.xterm]);

  const handlePointerDownCapture = useCallback(() => {
    terminal.xterm.focus();
  }, [terminal.xterm]);
  return <div ref={ref} className="w-full h-full" onPointerDownCapture={handlePointerDownCapture} />;
});
TerminalView.displayName = 'TerminalView';
