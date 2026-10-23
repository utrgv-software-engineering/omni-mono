import { App } from '@omniagents/chat-ui/App';
import { type ChatHost, ChatHostProvider } from '@omniagents/chat-ui/host';
import { RPCClientProvider, useRPCClient, useRPCConnected } from '@omniagents/chat-ui/rpc-context';
import { TerminalTabs } from '@omniagents/chat-ui/terminal/TerminalTabs';
import { UiConfigProvider, useUiConfig } from '@omniagents/chat-ui/ui-config';
import { uuidv4 } from '@omniagents/client/util/uuid';
import { Button } from '@omniagents/ui/button';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@omniagents/ui/resizable';
import { Toaster } from '@omniagents/ui/sonner';
import { isThemeDark, type OmniTheme } from '@omniagents/ui/themes';
import { TooltipProvider } from '@omniagents/ui/tooltip';
import { FolderOpenIcon, SquareTerminalIcon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { WorkspacePicker } from './components/WorkspacePicker';
import { createWebTerminals } from './web-terminals';

const host: ChatHost = {
  notifyError: (title, description) => {
    toast.error(title, { description });
  },
  // Whether the runtime offers realtime voice is the runtime's call.
  voiceMode: 'hosted',
  audioSettings: () => ({
    inputDeviceId: null,
    outputDeviceId: null,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  }),
};

/** The workspace the server announced in the page it served (`window.__OMNI_BOOT__`). */
function bootWorkspace(): string | null {
  const boot = (window as { __OMNI_BOOT__?: { workspace_root?: unknown } }).__OMNI_BOOT__;
  return typeof boot?.workspace_root === 'string' && boot.workspace_root ? boot.workspace_root : null;
}

/**
 * The omniagents web UI: the shared chat column against the runtime that served
 * this page, plus the web's own terminal drawer and workspace picker.
 */
export function WebApp({ theme }: { theme: OmniTheme }) {
  // The page itself is the runtime's address; its query carries the token.
  const connection = useMemo(() => ({ baseUrl: window.location.href }), []);
  return (
    <ChatHostProvider host={host}>
      <TooltipProvider delayDuration={300}>
        <UiConfigProvider connection={connection}>
          <RPCClientProvider>
            <WebChat />
          </RPCClientProvider>
        </UiConfigProvider>
        <Toaster theme={isThemeDark(theme) ? 'dark' : 'light'} position="bottom-right" closeButton richColors />
      </TooltipProvider>
    </ChatHostProvider>
  );
}

/** Server functions the runtime offers; terminals and folder browsing are product-provided. */
function useServerFunctions(): ReadonlySet<string> {
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

function WebChat() {
  const client = useRPCClient();
  const config = useUiConfig();
  const serverFunctions = useServerFunctions();
  const terminalSupported = serverFunctions.has('terminal.create');
  const folderBrowsingSupported = serverFunctions.has('fs_list_dir');
  const terminals = useMemo(() => createWebTerminals(client, config), [client, config]);
  // The shell owns the open conversation's id, so the terminal can share it
  // from the first chat on.
  const [sessionId, setSessionId] = useState<string>(() => config.session ?? uuidv4());
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [workspace, setWorkspace] = useState<string | null>(bootWorkspace);
  const [workspaceLocked, setWorkspaceLocked] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const variables = useMemo(() => (workspace ? { workspace_root: workspace } : undefined), [workspace]);

  // The address names the open conversation, so a reload or a bookmark
  // reopens it.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get('session') !== sessionId) {
      url.searchParams.set('session', sessionId);
      window.history.replaceState(window.history.state, '', url);
    }
  }, [sessionId]);

  const composerExtras = (
    <>
      {folderBrowsingSupported && !workspaceLocked && (
        <Button
          variant="ghost"
          size="icon-sm"
          className="rounded-full text-muted-foreground"
          aria-label="Choose workspace"
          title="Choose workspace"
          onClick={() => setPickerOpen(true)}
        >
          <FolderOpenIcon />
        </Button>
      )}
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
    <div className="h-dvh w-full bg-background text-foreground">
      <ResizablePanelGroup orientation="vertical">
        <ResizablePanel minSize="30%">
          <App
            sessionId={sessionId}
            onSessionChange={(id) => {
              // A new chat picks its own workspace until its first run.
              setSessionId(id ?? uuidv4());
              setWorkspaceLocked(false);
            }}
            onRunStarted={() => setWorkspaceLocked(true)}
            variables={variables}
            workspaceDir={workspace ?? undefined}
            composerExtras={composerExtras}
          />
        </ResizablePanel>
        {terminalOpen && terminalSupported && (
          <>
            <ResizableHandle />
            <ResizablePanel defaultSize="35%" minSize="15%">
              <TerminalTabs store={terminals} scope={sessionId} />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>
      {pickerOpen && (
        <WorkspacePicker
          client={client}
          initialPath={workspace ?? undefined}
          onSelect={(path) => {
            setWorkspace(path);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
