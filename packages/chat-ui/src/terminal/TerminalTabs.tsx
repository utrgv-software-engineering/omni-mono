import { useStore } from '@nanostores/react';
import type { TerminalState, TerminalStore } from '@omniagents/chat-ui/terminal/terminal-store';
import { TerminalView } from '@omniagents/chat-ui/terminal/TerminalView';
import { Button } from '@omniagents/ui/button';
import { cn } from '@omniagents/ui/cn';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@omniagents/ui/tabs';
import { Plus, SquareTerminal, X } from 'lucide-react';
import { memo, useCallback, useEffect, useMemo } from 'react';

type TerminalTabButtonProps = {
  store: TerminalStore;
  scope: string;
  terminalId: string;
  label: string;
  className: string;
  closeClassName: string;
};

const TerminalTabButton = memo(
  ({ store, scope, terminalId, label, className, closeClassName }: TerminalTabButtonProps) => {
    const handleClose = useCallback(
      (e: React.MouseEvent) => {
        e.stopPropagation();
        void store.destroy(scope, terminalId);
      },
      [store, scope, terminalId]
    );

    return (
      <div className={className}>
        <TabsTrigger
          value={terminalId}
          className="h-full min-w-0 flex-1 justify-start gap-1.5 border-0 bg-transparent p-0 text-xs leading-normal font-normal text-inherit after:hidden data-[state=active]:bg-transparent data-[state=active]:text-inherit data-[state=active]:shadow-none"
        >
          <SquareTerminal className="size-3.5 shrink-0" />
          <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{label}</span>
        </TabsTrigger>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className={closeClassName}
          onClick={handleClose}
          aria-label={`Close ${label}`}
        >
          <X />
        </Button>
      </div>
    );
  }
);
TerminalTabButton.displayName = 'TerminalTabButton';

type XtermPaneProps = {
  terminal: TerminalState;
  isActive: boolean;
  className: string;
};

const XtermPane = memo(({ terminal, isActive, className }: XtermPaneProps) => (
  <TabsContent value={terminal.id} forceMount className={cn(className, 'm-0 data-[state=inactive]:hidden')}>
    <TerminalView terminal={terminal} isActive={isActive} />
  </TabsContent>
));
XtermPane.displayName = 'XtermPane';

type TerminalTabsProps = {
  store: TerminalStore;
  /** Whose terminals: a chat column, a session. */
  scope: string;
};

/** A scope's terminals as tabs, opening one on first show. */
export const TerminalTabs = memo(({ store, scope }: TerminalTabsProps) => {
  const terminalsByScope = useStore(store.$terminalsByScope);
  const activeByScope = useStore(store.$activeByScope);
  const createErrorByScope = useStore(store.$createErrorByScope);
  const terminals = useMemo(() => terminalsByScope[scope] ?? [], [terminalsByScope, scope]);
  const activeId = activeByScope[scope] ?? null;
  const createError = createErrorByScope[scope] ?? null;

  useEffect(() => {
    void store.ensure(scope);
  }, [store, scope]);

  const handleNewTab = useCallback(() => {
    void store.create(scope).catch(() => {
      // The store recorded the error; the empty state shows it.
    });
  }, [store, scope]);

  const showEmptyState = terminals.length === 0 && createError !== null;

  return (
    <Tabs
      value={activeId ?? ''}
      onValueChange={(terminalId) => store.setActive(scope, terminalId)}
      className="relative flex h-full min-h-0 w-full flex-col gap-0"
    >
      <div className="flex min-h-8.5 w-full shrink-0 items-stretch gap-0.5 overflow-hidden border-b border-border bg-card px-1">
        <TabsList
          variant="line"
          className="flex min-w-0 w-auto flex-1 items-stretch justify-start gap-0.5 overflow-x-auto overflow-y-hidden rounded-none bg-transparent p-0 group-data-[orientation=horizontal]/tabs:h-auto scrollbar-none [&::-webkit-scrollbar]:hidden"
          aria-label="Terminals"
        >
          {terminals.map((t, i) => (
            <TerminalTabButton
              key={t.id}
              store={store}
              scope={scope}
              terminalId={t.id}
              label={`Terminal ${i + 1}`}
              className={cn(
                'mt-1 flex h-6.5 min-w-30 max-w-55 cursor-pointer select-none items-center gap-1 rounded-lg border border-transparent bg-transparent py-0 pr-1.5 pl-2.5 text-xs whitespace-nowrap text-foreground/80 transition-colors hover:bg-accent hover:text-foreground',
                t.id === activeId && 'border-border border-b-transparent bg-background text-foreground',
                !t.isRunning && 'text-destructive'
              )}
              closeClassName="ml-0.5 size-6 shrink-0 text-muted-foreground hover:text-foreground"
            />
          ))}
        </TabsList>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className="mt-1 ml-1 size-6.5 shrink-0 text-foreground/80"
          onClick={handleNewTab}
          aria-label="New terminal"
          title="New terminal"
        >
          <Plus className="size-4" />
        </Button>
      </div>
      <div className="relative w-full h-full min-h-0">
        {showEmptyState ? (
          <div className="flex w-full h-full items-center justify-center flex-col gap-2 p-6 text-center text-muted-foreground text-sm">
            {createError?.kind === 'not_ready' ? (
              <>
                <div>The agent is not running yet.</div>
                <div className="text-xs text-muted-foreground">
                  Terminals open in the agent&apos;s environment. Once it starts, click + above.
                </div>
              </>
            ) : (
              <>
                <div>Terminal unavailable.</div>
                <div className="text-xs text-muted-foreground">
                  {createError && 'message' in createError ? createError.message : 'Unknown error'}
                </div>
              </>
            )}
          </div>
        ) : (
          terminals.map((t) => (
            <XtermPane key={t.id} terminal={t} isActive={t.id === activeId} className="absolute inset-0" />
          ))
        )}
      </div>
    </Tabs>
  );
});
TerminalTabs.displayName = 'TerminalTabs';
