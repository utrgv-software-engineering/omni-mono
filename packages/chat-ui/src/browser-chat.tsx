import { Button } from '@omniagents/ui/button';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@omniagents/ui/resizable';
import { SquareTerminalIcon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { App, type AppProps } from './App';
import { useRPCClient, useRPCConnected } from './rpc-context';
import { createBrowserTerminals } from './terminal/browser-terminals';
import { TerminalTabs } from './terminal/TerminalTabs';
import { useUiConfig } from './ui-config';

/** Server functions the runtime offers. Terminals and folder browsing are
 * product functions a plain agent may not have. */
export function useServerFunctions(): ReadonlySet<string> {
  const client = useRPCClient();
  const connected = useRPCConnected();
  const [names, setNames] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    if (!connected) {
      return;
    }
    let current = true;
    void client
      .listServerFunctions()
      .then((functions) => current && setNames(new Set(functions.map((f) => f.name))))
      .catch(() => {});
    return () => {
      current = false;
    };
  }, [client, connected]);
  return names;
}

export type BrowserChatProps = AppProps & {
  /** The open conversation; the page owns it so the terminal shares it. */
  sessionId: string;
};

/**
 * The shared chat column for a browser page talking straight to a runtime
 * (the omniagents web UI, Studio's Playground), with a terminal drawer when
 * the runtime offers terminals. Needs the UiConfig and RPC client providers.
 */
export function BrowserChat({ composerExtras, ...props }: BrowserChatProps) {
  const client = useRPCClient();
  const config = useUiConfig();
  const terminalSupported = useServerFunctions().has('terminal.create');
  const terminals = useMemo(() => createBrowserTerminals(client, config), [client, config]);
  const [terminalOpen, setTerminalOpen] = useState(false);

  const extras = (
    <>
      {composerExtras}
      {terminalSupported && !config.minimal && (
        <Button
          variant="ghost"
          size="icon-sm"
          className="rounded-full text-muted-foreground data-[active=true]:text-foreground"
          data-active={terminalOpen}
          aria-label={terminalOpen ? 'Hide terminal' : 'Show terminal'}
          title={terminalOpen ? 'Hide terminal' : 'Show terminal'}
          onClick={() => setTerminalOpen((open) => !open)}
        >
          <SquareTerminalIcon />
        </Button>
      )}
    </>
  );

  return (
    <ResizablePanelGroup orientation="vertical">
      <ResizablePanel minSize="30%">
        <App {...props} composerExtras={extras} />
      </ResizablePanel>
      {terminalOpen && terminalSupported && (
        <>
          <ResizableHandle />
          <ResizablePanel defaultSize="35%" minSize="15%">
            <TerminalTabs store={terminals} scope={props.sessionId} />
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  );
}
