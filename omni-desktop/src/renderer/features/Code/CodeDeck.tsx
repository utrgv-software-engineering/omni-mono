import { DndContext, type DragEndEvent, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import { arrayMove, horizontalListSortingStrategy, SortableContext, useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { useStore } from '@nanostores/react';
import { motion } from 'framer-motion';
import { Archive, Ellipsis, PanelRight, Plus, RefreshCw } from 'lucide-react';
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { ClosableTab, ClosableTabsList } from '@/renderer/ds/ClosableTabs';
import { cn } from '@/renderer/ds/cn';
import { Button } from '@/renderer/ds/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/renderer/ds/ui/dropdown-menu';
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from '@/renderer/ds/ui/empty';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/renderer/ds/ui/resizable';
import { SidebarTrigger, useSidebar } from '@/renderer/ds/ui/sidebar';
import { Tabs } from '@/renderer/ds/ui/tabs';
import { Toggle } from '@/renderer/ds/ui/toggle';
import { AgentsSurface } from '@/renderer/features/Agents';
import { $appLaunchRequest, clearAppLaunchRequest } from '@/renderer/features/AppControl/app-launch-bridge';
import { BrowserView } from '@/renderer/features/Browser/BrowserView';
import { $previewRequest, clearPreviewRequest } from '@/renderer/features/Code/preview-bridge';
import { ConsoleStarted } from '@/renderer/features/Console/ConsoleRunning';
import { JobsSurface } from '@/renderer/features/Jobs';
import { $agentStatuses } from '@/renderer/services/agent-process';
import { $columnActivity, activityStatusText } from '@/renderer/services/column-activity';
import { persistedStoreApi } from '@/renderer/services/store';
import { FADE_DURATION_S } from '@/renderer/theme/motion';
import type { AppDescriptor, AppId } from '@/shared/app-registry';
import { APP_REGISTRY } from '@/shared/app-registry';
import type { AutoLaunchPhase } from '@/shared/machines/auto-launch.machine';
import type { CodeTab, CodeTabId } from '@/shared/types';
import { firstSource } from '@/shared/types';

import { AppIcon } from './AppIcon';
import { CodeTabContent } from './CodeTabContent';
import { ColumnAura } from './ColumnAura';
import { $codeTabPhases, codeApi, finishRemoval } from './state';
import { useRecentConversations } from './use-recent-conversations';

/** At or below this width the apps sidecar stacks under the chat. */
const PHONE_WIDTH = 540;

const CodeDeckHeader = memo(({ onNewSession }: { onNewSession: () => void }) => {
  const { isMobile, state: sidebarState } = useSidebar();
  const navigationHidden = isMobile || sidebarState === 'collapsed';
  return (
    <div className={cn('flex h-10 items-center justify-between pl-4 pr-4 border-b border-border bg-card')}>
      {navigationHidden && <SidebarTrigger className="-ml-1.5" size="icon-sm" aria-label="Open navigation" />}
      <div className="flex items-center gap-2 ml-auto">
        <Button size="sm" variant="ghost" onClick={onNewSession}>
          <Plus className="size-4" />
          New chat
        </Button>
      </div>
    </div>
  );
});
CodeDeckHeader.displayName = 'CodeDeckHeader';

/** Boot-phase wording for the status line (pre-chat agent lifecycle). */
const BOOT_PHASE_LABELS: Partial<Record<AutoLaunchPhase, string>> = {
  checking: 'Checking runtime…',
  installing: 'Installing runtime…',
  configChecking: 'Checking configuration…',
  starting: 'Starting agent…',
};

/** Re-render once a minute so relative "Started …" labels stay fresh. */
const useNowMinute = (): number => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);
  return now;
};

/**
 * Identity stamp for a session column. Relative while fresh, but past 24h it
 * includes the date + time of day — two columns started "yesterday" must not
 * collapse into the same label, since this line exists to tell them apart.
 */
const startedLabel = (tab: CodeTab): string | null => {
  if (!tab.createdAt) {
    return null;
  }
  const ageMs = Date.now() - tab.createdAt;
  const started = new Date(tab.createdAt);
  if (ageMs < 60_000) {
    return 'Started just now';
  }
  if (ageMs < 60 * 60_000) {
    return `Started ${Math.floor(ageMs / 60_000)}m ago`;
  }
  if (ageMs < 24 * 60 * 60_000) {
    return `Started ${Math.floor(ageMs / (60 * 60_000))}h ago`;
  }
  return `Started ${started.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${started.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
};

const shortRoutineSchedule = (schedule: string): string => schedule.replace(/ · next .+$/, '');

/**
 * Glanceable "now doing X" under a column header: agent boot phase while
 * launching, then the live run state (tool line / waiting-for-approval)
 * published by the embedded chat. Renders nothing when the column is idle.
 */
const ColumnStatusLine = memo(({ tabId }: { tabId: CodeTabId }) => {
  const activity = useStore($columnActivity, { keys: [tabId] })[tabId];
  const bootPhase = useStore($codeTabPhases, { keys: [tabId] })[tabId];
  const bootText = bootPhase ? BOOT_PHASE_LABELS[bootPhase] : undefined;
  const text = bootText ?? activityStatusText(activity);
  if (!text) {
    return null;
  }
  const waiting = !bootText && !!activity?.pendingApproval;
  return (
    // Deliberately NOT a live region: with many columns, per-column
    // role="status" produced interleaved narration. The StatusAnnouncer at
    // the App root is the one screen-reader voice for column transitions.
    <div className="flex items-center gap-1.5 min-w-0 pl-4 pr-4 pt-px pb-1">
      <span
        className={cn(
          'size-1.5 shrink-0 animate-pulse rounded-full bg-primary motion-reduce:animate-none',
          waiting && 'bg-warning animate-none'
        )}
        aria-hidden="true"
      />
      {/* Keyed by text: each phase/tool transition ticks in with a small
             fade+rise instead of snapping — the session-birth sequence reads as
             one line morphing through its states. (MotionConfig disables this
             under reduced motion.) */}
      <motion.span
        key={text}
        initial={{ opacity: 0, y: 3 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: FADE_DURATION_S }}
        className="text-xs text-muted-foreground overflow-hidden text-ellipsis whitespace-nowrap min-w-0"
        title={text}
      >
        {text}
      </motion.span>
    </div>
  );
});
ColumnStatusLine.displayName = 'ColumnStatusLine';

const CodeSessionHeader = memo(
  ({
    label,
    subLabel,
    statusTabId,
    routineName,
    routineSchedule,
    actions,
    onArchive,
  }: {
    label: string;
    /** Identity context when there is no routine banner (e.g. "Started 2h ago"). */ subLabel?: string | null;
    /** When set, a live ColumnStatusLine for this tab renders under the header. */ statusTabId?: CodeTabId;
    routineName?: string | null;
    routineSchedule?: string | null;
    actions?: React.ReactNode;
    onArchive?: () => void;
  }) => {
    return (
      <>
        <div className="relative flex items-center justify-between pl-4 pr-2 pt-1 pb-1 min-h-8 bg-transparent">
          <div className={cn('flex items-center', 'gap-2', 'min-w-0')}>
            <span
              className="text-xs font-medium text-muted-foreground tracking-normal overflow-hidden text-ellipsis whitespace-nowrap"
              title={label}
            >
              {label}
            </span>
            {subLabel && (
              <span
                className="text-xs text-muted-foreground overflow-hidden text-ellipsis whitespace-nowrap shrink min-w-0"
                title={subLabel}
              >
                {subLabel}
              </span>
            )}
          </div>
          <div className={cn('flex items-center', 'gap-1')}>
            {actions}
            {onArchive && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button type="button" variant="ghost" size="icon-sm" aria-label="Session menu" title="Session menu">
                    <Ellipsis className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <>
                  <DropdownMenuContent>
                    <DropdownMenuItem onClick={onArchive}>
                      <Archive />
                      Archive session
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </>
              </DropdownMenu>
            )}
          </div>
        </div>
        {routineName && (
          <div
            className={cn(
              'flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-4 pr-4 pt-0.5 pb-1 bg-muted border-t border-border border-b border-border'
            )}
          >
            <span className="items-center bg-secondary border border-border rounded-full text-secondary-foreground inline-flex shrink-0 text-xs font-semibold gap-1 px-2 py-px">
              <RefreshCw className="size-3" />
              Routine
            </span>
            <span
              className="text-muted-foreground min-w-40 flex-1 text-xs min-w-0 overflow-hidden text-ellipsis whitespace-nowrap"
              title={routineName}
            >
              {routineName}
            </span>
            {routineSchedule && (
              <span
                className="text-muted-foreground shrink-0 text-xs overflow-hidden text-ellipsis whitespace-nowrap"
                title={routineSchedule}
              >
                {shortRoutineSchedule(routineSchedule)}
              </span>
            )}
          </div>
        )}
        {statusTabId && <ColumnStatusLine tabId={statusTabId} />}
      </>
    );
  }
);
CodeSessionHeader.displayName = 'CodeSessionHeader';

type SidecarBodyProps = {
  app: AppDescriptor;
  originTabId: CodeTabId;
  /** Session bound to the origin column — the Agents surface reads the
   *  subagents store by session id. */
  sessionId?: string;
  filesHost: HTMLDivElement;
  gitHost: HTMLDivElement;
  reviewHost: HTMLDivElement;
  runtime:
    | {
        uiUrl?: string;
        authToken?: string;
        workspaceId?: string;
        environmentId?: string;
        environmentGeneration?: number;
        services?: Record<string, string>;
      }
    | undefined;
  previewUrl?: string;
  onPreviewUrlChange?: (url: string) => void;
};

/**
 * One persistent sidecar app body. CodeDeck portals it into a stable detached
 * host, and the active layout adopts that host. This preserves terminal
 * scrollback, browser navigation, and editor state across tab and layout
 * switches.
 */
const SidecarBody = memo(
  ({
    app,
    originTabId,
    sessionId,
    filesHost,
    gitHost,
    reviewHost,
    runtime,
    previewUrl,
    onPreviewUrlChange,
  }: SidecarBodyProps) => {
    let body: React.ReactNode = null;
    if (app.kind === 'builtin-browser') {
      body = (
        <BrowserView
          tabsetId={`dock:${originTabId}`}
          registryScope="column"
          registryTabId={originTabId}
          src={previewUrl}
          onUrlChange={onPreviewUrlChange}
        />
      );
    } else if (app.kind === 'builtin-terminal') {
      body = runtime ? (
        <ConsoleStarted tabId={originTabId} />
      ) : (
        <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
          Terminal is available after the session starts.
        </div>
      );
    } else if (app.kind === 'builtin-files') {
      body = runtime?.environmentId ? (
        <TabContentSlot host={filesHost} />
      ) : (
        <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
          Files are available after the session starts.
        </div>
      );
    } else if (app.kind === 'builtin-git') {
      body = runtime?.environmentId ? (
        <TabContentSlot host={gitHost} />
      ) : (
        <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
          Git is available after the session starts.
        </div>
      );
    } else if (app.kind === 'builtin-review') {
      body = runtime?.environmentId ? (
        <TabContentSlot host={reviewHost} />
      ) : (
        <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
          Review is available after the session starts.
        </div>
      );
    } else if (app.kind === 'builtin-agents') {
      body =
        sessionId && runtime ? (
          <AgentsSurface sessionId={sessionId} runtime={runtime} />
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
            Agents are available after the session starts.
          </div>
        );
    } else if (app.kind === 'builtin-jobs') {
      body =
        sessionId && runtime ? (
          <JobsSurface sessionId={sessionId} />
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
            Jobs are available after the session starts.
          </div>
        );
    }

    return <div className="absolute inset-0">{body}</div>;
  }
);
SidecarBody.displayName = 'SidecarBody';

/** One drag-reorderable sidecar app tab (pointer drag only, 6px slop —
 *  plain clicks still activate). */
const SortableSidecarTab = memo(({ app, onClose }: { app: AppDescriptor; onClose: () => void }) => {
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: app.id });
  return (
    <ClosableTab
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      value={app.id}
      label={app.label}
      icon={<AppIcon icon={app.icon} size={16} />}
      onClose={onClose}
      wrapperClassName={cn(isDragging && 'opacity-60')}
      {...listeners}
    />
  );
});
SortableSidecarTab.displayName = 'SortableSidecarTab';

/**
 * Non-sortable adjacent column hosting the open app tabs bound to an origin
 * session. Every open app remains mounted while inactive so its browser,
 * terminal, editor, or webview state survives tab switches.
 */
const SidecarColumn = memo(
  ({
    apps,
    activeAppId,
    availableApps,
    getAppHost,
    onActivate,
    onCloseApp,
    onOpenApp,
    onReorderApps,
  }: {
    apps: AppDescriptor[];
    activeAppId?: AppId;
    availableApps: AppDescriptor[];
    getAppHost: (appId: AppId) => HTMLDivElement;
    onActivate: (appId: AppId) => void;
    onCloseApp: (appId: AppId) => void;
    onOpenApp: (appId: AppId) => void;
    onReorderApps: (appIds: AppId[]) => void;
  }) => {
    const addableApps = availableApps.filter((app) => !apps.some((openApp) => openApp.id === app.id));

    const tabSensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
    const handleTabDragEnd = useCallback(
      (event: DragEndEvent) => {
        const { active, over } = event;
        if (!over || active.id === over.id) {
          return;
        }
        const ids = apps.map((app) => app.id);
        const oldIndex = ids.indexOf(String(active.id) as AppId);
        const newIndex = ids.indexOf(String(over.id) as AppId);
        if (oldIndex < 0 || newIndex < 0) {
          return;
        }
        onReorderApps(arrayMove(ids, oldIndex, newIndex));
      },
      [apps, onReorderApps]
    );

    return (
      <div className="flex flex-col w-full h-full min-w-0 min-h-0 overflow-hidden bg-transparent">
        <div className="flex flex-1 flex-col min-w-0 min-h-0 relative overflow-hidden bg-card">
          <Tabs value={activeAppId ?? ''} onValueChange={onActivate} className="h-full min-h-0 gap-0">
            {/* Same 36px row rhythm as the Review toolbar and the browser's
                own tab strip below — mismatched row heights read as double
                spacing when the strips stack. */}
            <div className="flex items-center gap-1 min-h-9 pl-2 pr-2 shrink-0">
              {apps.length > 0 ? (
                <ClosableTabsList aria-label="Open column apps">
                  <DndContext sensors={tabSensors} onDragEnd={handleTabDragEnd}>
                    <SortableContext items={apps.map((app) => app.id)} strategy={horizontalListSortingStrategy}>
                      {apps.map((app) => (
                        <SortableSidecarTab key={app.id} app={app} onClose={() => onCloseApp(app.id)} />
                      ))}
                    </SortableContext>
                  </DndContext>
                </ClosableTabsList>
              ) : (
                <div className="flex min-w-0 flex-1 items-center px-2 text-xs font-medium text-muted-foreground">
                  Apps
                </div>
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    className="size-7 shrink-0"
                    aria-label="Open app tab"
                    title="Open app tab"
                  >
                    <Plus className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {addableApps.length > 0 ? (
                    addableApps.map((app) => (
                      <DropdownMenuItem key={app.id} onClick={() => onOpenApp(app.id)}>
                        <AppIcon icon={app.icon} size={16} />
                        {app.label}
                      </DropdownMenuItem>
                    ))
                  ) : (
                    <DropdownMenuItem disabled>No other apps available</DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <div className="flex-1 min-h-0 relative">
              {apps.length === 0 ? (
                <Empty className="absolute inset-0 rounded-none border-0">
                  <EmptyHeader>
                    <EmptyTitle className="text-base">Open an app</EmptyTitle>
                    <EmptyDescription>Choose an app for this session’s sidecar.</EmptyDescription>
                  </EmptyHeader>
                  <EmptyContent className="grid max-w-md grid-cols-1 gap-2 sm:grid-cols-2">
                    {availableApps.map((app) => (
                      <Button
                        key={app.id}
                        type="button"
                        variant="outline"
                        className="w-full justify-start"
                        onClick={() => onOpenApp(app.id)}
                      >
                        <AppIcon icon={app.icon} size={16} />
                        {app.label}
                      </Button>
                    ))}
                  </EmptyContent>
                </Empty>
              ) : (
                apps.map((app) => (
                  <div key={app.id} className={cn('absolute inset-0', app.id !== activeAppId && 'hidden')}>
                    <TabContentSlot host={getAppHost(app.id)} />
                  </div>
                ))
              )}
            </div>
          </Tabs>
        </div>
      </div>
    );
  }
);
SidecarColumn.displayName = 'SidecarColumn';

const CodeSessionPane = memo(
  ({
    tab,
    label,
    routineName,
    routineSchedule,
    actions,
    onArchive,
    isVisible,
    content,
  }: {
    tab: CodeTab;
    label: string;
    routineName?: string | null;
    routineSchedule?: string | null;
    actions?: React.ReactNode;
    onArchive: (id: CodeTabId) => void;
    isVisible: boolean;
    /** The tab's content slot — CodeDeck portals the persistent CodeTabContent here. */ content: React.ReactNode;
  }) => {
    useNowMinute();

    return (
      // Park inactive panes with real geometry: collapsing them to zero makes
      // transcript scrollers forget their reading position.
      <div
        className={cn(
          'w-full h-full flex flex-col bg-card',
          isVisible ? 'relative' : 'absolute inset-0 invisible pointer-events-none'
        )}
        inert={!isVisible}
        aria-hidden={!isVisible}
      >
        <ColumnAura tabId={tab.id} />
        <CodeSessionHeader
          label={label}
          subLabel={routineName ? null : startedLabel(tab)}
          statusTabId={tab.id}
          routineName={routineName}
          routineSchedule={routineSchedule}
          actions={actions}
          onArchive={() => onArchive(tab.id)}
        />

        <div className="flex-1 min-h-0 relative">{content}</div>
      </div>
    );
  }
);
CodeSessionPane.displayName = 'CodeSessionPane';

/**
 * Layout-transparent adopter for a column's persistent content. CodeDeck
 * portals each session tab's SINGLE long-lived CodeTabContent into a STABLE
 * detached <div> (the host); the session's pane renders one TabContentSlot
 * that imperatively appends that host into its own DOM, so re-renders of the
 * pane MOVE the rendered session instead of remounting it — no agent
 * reconnect, no transcript reload, composer drafts survive. The portal
 * container must stay identity-stable: React reconciles createPortal by
 * container, so portaling into per-mode slot elements would remount.
 */
const TabContentSlot = memo(({ host }: { host: HTMLDivElement }) => {
  const ref = useCallback(
    (el: HTMLDivElement | null) => {
      if (el) {
        el.appendChild(host);
      }
    },
    [host]
  );
  return <div ref={ref} className="contents" />;
});
TabContentSlot.displayName = 'TabContentSlot';

export const CodeDeck = memo(() => {
  const store = useStore(persistedStoreApi.$atom);
  const statuses = useStore($agentStatuses);
  const tabs = useMemo(() => store.codeTabs ?? [], [store.codeTabs]);
  const previousTabIds = useRef(new Set(tabs.map((tab) => tab.id)));
  useEffect(() => {
    const current = new Set(tabs.map((tab) => tab.id));
    for (const id of previousTabIds.current) {
      if (!current.has(id)) {
        finishRemoval(id);
      }
    }
    previousTabIds.current = current;
  }, [tabs]);
  // One persistent CodeTabContent per session tab, portaled into a stable
  // detached host that the live layout mode's TabContentSlot adopts.
  const sessionTabs = tabs;
  const contentHostsRef = useRef<Map<CodeTabId, HTMLDivElement>>(new Map());
  const filesHostsRef = useRef<Map<CodeTabId, HTMLDivElement>>(new Map());
  const gitHostsRef = useRef<Map<CodeTabId, HTMLDivElement>>(new Map());
  const reviewHostsRef = useRef<Map<CodeTabId, HTMLDivElement>>(new Map());
  const sidecarHostsRef = useRef<Map<string, HTMLDivElement>>(new Map());
  const getContentHost = useCallback((tabId: CodeTabId): HTMLDivElement => {
    let host = contentHostsRef.current.get(tabId);
    if (!host) {
      host = document.createElement('div');
      host.style.display = 'contents';
      contentHostsRef.current.set(tabId, host);
    }
    return host;
  }, []);
  const getFilesHost = useCallback((tabId: CodeTabId): HTMLDivElement => {
    let host = filesHostsRef.current.get(tabId);
    if (!host) {
      host = document.createElement('div');
      host.style.width = '100%';
      host.style.height = '100%';
      host.style.minHeight = '0';
      filesHostsRef.current.set(tabId, host);
    }
    return host;
  }, []);
  const getGitHost = useCallback((tabId: CodeTabId): HTMLDivElement => {
    let host = gitHostsRef.current.get(tabId);
    if (!host) {
      host = document.createElement('div');
      host.style.width = '100%';
      host.style.height = '100%';
      host.style.minHeight = '0';
      gitHostsRef.current.set(tabId, host);
    }
    return host;
  }, []);
  const getReviewHost = useCallback((tabId: CodeTabId): HTMLDivElement => {
    let host = reviewHostsRef.current.get(tabId);
    if (!host) {
      host = document.createElement('div');
      host.style.width = '100%';
      host.style.height = '100%';
      host.style.minHeight = '0';
      reviewHostsRef.current.set(tabId, host);
    }
    return host;
  }, []);
  const getSidecarHost = useCallback((tabId: CodeTabId, appId: AppId): HTMLDivElement => {
    const key = `${tabId}:${appId}`;
    let host = sidecarHostsRef.current.get(key);
    if (!host) {
      host = document.createElement('div');
      host.style.width = '100%';
      host.style.height = '100%';
      host.style.minHeight = '0';
      host.style.position = 'relative';
      sidecarHostsRef.current.set(key, host);
    }
    return host;
  }, []);
  useEffect(() => {
    const live = new Set<string>(sessionTabs.map((t) => t.id));
    for (const id of [...contentHostsRef.current.keys()]) {
      if (!live.has(id)) {
        contentHostsRef.current.delete(id);
      }
    }
    for (const id of [...filesHostsRef.current.keys()]) {
      if (!live.has(id)) {
        filesHostsRef.current.delete(id);
      }
    }
    for (const id of [...gitHostsRef.current.keys()]) {
      if (!live.has(id)) {
        gitHostsRef.current.delete(id);
      }
    }
    for (const id of [...reviewHostsRef.current.keys()]) {
      if (!live.has(id)) {
        reviewHostsRef.current.delete(id);
      }
    }
    for (const key of [...sidecarHostsRef.current.keys()]) {
      if (![...live].some((id) => key.startsWith(`${id}:`))) {
        sidecarHostsRef.current.delete(key);
      }
    }
  }, [sessionTabs]);
  const activeTabId = store.activeCodeTabId ?? tabs[0]?.id ?? null;
  const [previewUrls, setPreviewUrls] = useState<Record<CodeTabId, string>>({});
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const handler = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, []);

  useEffect(() => {
    const firstTab = tabs[0];
    if (!activeTabId && firstTab) {
      codeApi.setActiveTab(firstTab.id);
    }
  }, [activeTabId, tabs]);

  // React to agent-triggered preview requests. We subscribe via `listen` rather
  // than `useStore` so every atom update fires — a rapid burst of requests all
  // get applied instead of being coalesced to the last-rendered value.
  const activeTabIdRef = useRef(activeTabId);
  const firstTabIdRef = useRef(tabs[0]?.id);
  activeTabIdRef.current = activeTabId;
  firstTabIdRef.current = tabs[0]?.id;
  useEffect(() => {
    const seen = new Set<string>();
    const unsubscribe = $previewRequest.listen((req) => {
      if (!req || seen.has(req.id)) {
        return;
      }
      seen.add(req.id);
      const targetTabId = (req.tabId as CodeTabId | undefined) ?? activeTabIdRef.current ?? firstTabIdRef.current;
      if (!targetTabId) {
        return;
      }
      setPreviewUrls((prev) => ({ ...prev, [targetTabId]: req.url }));
      void codeApi.openSidecarApp(targetTabId, 'browser');
      clearPreviewRequest();
    });
    return unsubscribe;
  }, []);

  // `launch_app` client tool → open (not toggle) a dock app in a column so its
  // webview mounts and becomes drivable. Same fire-every-update listener pattern
  // as the preview bridge above.
  useEffect(() => {
    const seen = new Set<string>();
    const unsubscribe = $appLaunchRequest.listen((req) => {
      if (!req || seen.has(req.id)) {
        return;
      }
      seen.add(req.id);
      void codeApi.openSidecarApp(req.tabId as CodeTabId, req.appId);
      clearAppLaunchRequest();
    });
    return unsubscribe;
  }, []);

  const projectMap = useMemo(() => {
    const map = new Map<string, { label: string; workspaceDir: string | undefined }>();
    for (const p of store.projects) {
      const s = firstSource(p);
      map.set(p.id, { label: p.label, workspaceDir: s?.kind === 'local' ? s.workspaceDir : undefined });
    }
    return map;
  }, [store.projects]);

  const appRegistry = APP_REGISTRY;

  // Conversation titles come from the launcher index ∪ the live session
  // listing, so open columns are titled even when the conversation predates
  // the index (migrated or resumed-from-server sessions).
  // Recent conversations moved to the app sidebar's Sessions section; the
  // deck still needs the title union for open-column labels.
  const { sessionTitles } = useRecentConversations(tabs);

  const resolveLabel = useCallback(
    (tab: CodeTab) => {
      if (!tab.projectId) {
        // Chat column: the conversation's title once it has one, else the
        // fresh-column label.
        return (tab.sessionId ? sessionTitles.get(tab.sessionId) : undefined) ?? 'New chat';
      }
      return projectMap.get(tab.projectId)?.label ?? 'Unknown';
    },
    [projectMap, sessionTitles]
  );

  const resolveRoutineName = useCallback((tab: CodeTab) => tab.routineName ?? null, []);
  const resolveRoutineSchedule = useCallback((tab: CodeTab) => tab.routineSchedule ?? null, []);

  const handleNewSession = useCallback(() => {
    codeApi.addTab();
  }, []);

  const handleArchive = useCallback(
    async (id: CodeTabId) => {
      setPreviewUrls((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
      const tab = tabs.find((candidate) => candidate.id === id);
      const title = tab?.sessionId ? sessionTitles.get(tab.sessionId) : undefined;
      await codeApi.archiveTab(id, title);
    },
    [sessionTitles, tabs]
  );

  const handleOpenSidecarApp = useCallback((tabId: CodeTabId, app: AppId) => {
    void codeApi.openSidecarApp(tabId, app);
  }, []);

  const handleActivateSidecarApp = useCallback((tabId: CodeTabId, app: AppId) => {
    void codeApi.setActiveSidecarApp(tabId, app);
  }, []);

  const handleCloseSidecarApp = useCallback((tabId: CodeTabId, app: AppId) => {
    void codeApi.closeSidecarApp(tabId, app);
  }, []);

  const handleReorderSidecarApps = useCallback((tabId: CodeTabId, appIds: AppId[]) => {
    void codeApi.reorderSidecarApps(tabId, appIds);
  }, []);

  const handleSidecarOpenChange = useCallback((tabId: CodeTabId, open: boolean) => {
    void codeApi.setSidecarOpen(tabId, open);
  }, []);

  const handlePreviewUrlChange = useCallback((tabId: CodeTabId, url: string) => {
    // Idempotent on purpose: BrowserView re-reports the active URL whenever
    // its sync effect re-runs, and the inline arrow prop below renews its
    // identity every render. Returning the same reference lets React bail
    // out instead of looping render → new arrow → effect → setState.
    setPreviewUrls((prev) => (prev[tabId] === url ? prev : { ...prev, [tabId]: url }));
  }, []);

  const activeTab = tabs.find((t) => t.id === activeTabId) ?? tabs[0] ?? null;

  const renderSessionActions = useCallback(
    (tab: CodeTab) => (
      <Toggle
        size="sm"
        pressed={tab.sidecarOpen ?? Boolean(tab.sidecarAppIds?.length)}
        aria-label={(tab.sidecarOpen ?? Boolean(tab.sidecarAppIds?.length)) ? 'Hide apps' : 'Show apps'}
        title={(tab.sidecarOpen ?? Boolean(tab.sidecarAppIds?.length)) ? 'Hide apps' : 'Show apps'}
        onPressedChange={(open) => handleSidecarOpenChange(tab.id, open)}
      >
        <PanelRight className="size-4" />
      </Toggle>
    ),
    [handleSidecarOpenChange]
  );

  type SidecarState = {
    apps: AppDescriptor[];
    activeAppId: AppId | undefined;
    availableApps: AppDescriptor[];
    visible: boolean;
  };
  const sidecarStateByTab = new Map<CodeTabId, SidecarState>();
  const visibleSidecarsByTab = new Map<CodeTabId, SidecarState>();
  for (const tab of sessionTabs) {
    const apps = (tab.sidecarAppIds ?? [])
      .map((appId) => appRegistry.find((app) => app.id === appId && app.columnScoped && app.id !== 'chat'))
      .filter((app): app is AppDescriptor => Boolean(app));
    const agentStatus = statuses[tab.id];
    const runtime = agentStatus?.type === 'running' ? agentStatus.data : undefined;
    const availableApps = appRegistry.filter((app) => {
      if (!app.columnScoped || app.id === 'chat') {
        return false;
      }
      if (
        app.kind === 'builtin-files' ||
        app.kind === 'builtin-git' ||
        app.kind === 'builtin-review' ||
        app.kind === 'builtin-terminal' ||
        app.kind === 'builtin-agents' ||
        app.kind === 'builtin-jobs'
      ) {
        return Boolean(runtime);
      }
      return true;
    });
    const activeAppId =
      apps.length === 0
        ? undefined
        : apps.some((app) => app.id === tab.activeSidecarAppId)
          ? (tab.activeSidecarAppId as AppId)
          : apps[apps.length - 1]!.id;
    const sidecarState = {
      apps,
      activeAppId,
      availableApps,
      visible: tab.sidecarOpen ?? apps.length > 0,
    };
    sidecarStateByTab.set(tab.id, sidecarState);
    if (sidecarState.visible) {
      visibleSidecarsByTab.set(tab.id, sidecarState);
    }
  }
  const activeSidecar = activeTab ? visibleSidecarsByTab.get(activeTab.id) : undefined;
  return (
    <div className="flex flex-col w-full h-full min-h-0 overflow-hidden bg-background">
      <CodeDeckHeader onNewSession={handleNewSession} />

      {/* Archiving the last session leaves the deck genuinely empty — nothing
               re-mints a column behind the user's back. */}
      {tabs.length === 0 && (
        <div className="flex-1 min-h-0 flex items-center justify-center">
          <Empty>
            <EmptyHeader>
              <EmptyTitle className="text-base">No active sessions</EmptyTitle>
              <EmptyDescription>
                Start a chat, or launch an app from the header. Archived sessions can be restored from the sidebar.
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Button variant="default" onClick={handleNewSession}>
                <Plus />
                New chat
              </Button>
            </EmptyContent>
          </Empty>
        </div>
      )}
      {tabs.length > 0 && (
        <div className="flex-1 min-h-0 flex flex-col [@media(min-width:541px)]:flex-row">
          <ResizablePanelGroup
            key={`focus:${activeTab?.id ?? 'none'}:${activeSidecar ? 'split' : 'chat'}`}
            orientation={viewportWidth <= PHONE_WIDTH ? 'vertical' : 'horizontal'}
            className="flex-1 min-w-0 min-h-0"
          >
            <ResizablePanel
              id={`focus:${activeTab?.id ?? 'none'}`}
              defaultSize={activeSidecar ? '50%' : '100%'}
              minSize={activeSidecar ? '30%' : undefined}
              className="relative flex-1 min-w-0 min-h-0"
            >
              {tabs.map((tab) => {
                return (
                  <CodeSessionPane
                    key={tab.id}
                    tab={tab}
                    label={resolveLabel(tab)}
                    routineName={resolveRoutineName(tab)}
                    routineSchedule={resolveRoutineSchedule(tab)}
                    actions={renderSessionActions(tab)}
                    onArchive={handleArchive}
                    isVisible={tab.id === activeTab?.id}
                    content={<TabContentSlot host={getContentHost(tab.id)} />}
                  />
                );
              })}
            </ResizablePanel>
            {activeTab && activeSidecar && (
              <>
                <ResizableHandle withHandle />
                <ResizablePanel
                  id={`focus-sidecar:${activeTab.id}`}
                  defaultSize="50%"
                  minSize="30%"
                  className="flex-1 min-w-0 min-h-0"
                >
                  <SidecarColumn
                    apps={activeSidecar.apps}
                    activeAppId={activeSidecar.activeAppId}
                    availableApps={activeSidecar.availableApps}
                    getAppHost={(appId) => getSidecarHost(activeTab.id, appId)}
                    onActivate={(appId) => handleActivateSidecarApp(activeTab.id, appId)}
                    onCloseApp={(appId) => handleCloseSidecarApp(activeTab.id, appId)}
                    onOpenApp={(appId) => handleOpenSidecarApp(activeTab.id, appId)}
                    onReorderApps={(appIds) => handleReorderSidecarApps(activeTab.id, appIds)}
                  />
                </ResizablePanel>
              </>
            )}
          </ResizablePanelGroup>
        </div>
      )}
      {sessionTabs.map((tab) =>
        createPortal(
          <CodeTabContent
            tab={tab}
            isVisible={tab.id === activeTab?.id}
            activeApp={sidecarStateByTab.get(tab.id)?.activeAppId ?? 'chat'}
            onActiveAppChange={(app) => handleOpenSidecarApp(tab.id, app)}
            uiMinimal
            filesHost={getFilesHost(tab.id)}
            gitHost={getGitHost(tab.id)}
            reviewHost={getReviewHost(tab.id)}
          />,

          getContentHost(tab.id),
          `tab-content-${tab.id}`
        )
      )}
      {sessionTabs.flatMap((tab) => {
        const sidecar = sidecarStateByTab.get(tab.id);
        if (!sidecar) {
          return [];
        }
        const agentStatus = statuses[tab.id];
        const runtime = agentStatus?.type === 'running' ? agentStatus.data : undefined;
        return sidecar.apps.map((app) =>
          createPortal(
            <SidecarBody
              app={app}
              originTabId={tab.id}
              sessionId={tab.sessionId}
              filesHost={getFilesHost(tab.id)}
              gitHost={getGitHost(tab.id)}
              reviewHost={getReviewHost(tab.id)}
              runtime={runtime}
              previewUrl={previewUrls[tab.id]}
              onPreviewUrlChange={(url) => handlePreviewUrlChange(tab.id, url)}
            />,
            getSidecarHost(tab.id, app.id),
            `sidecar-content-${tab.id}-${app.id}`
          )
        );
      })}
    </div>
  );
});
CodeDeck.displayName = 'CodeDeck';
