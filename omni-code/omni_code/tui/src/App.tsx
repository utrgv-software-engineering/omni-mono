import React, { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Box, Text, useApp, useInput, useStdin, useStdout, type Key } from "ink";
import type { Project, Ticket, OmniSession, SandboxConfig, Container } from "./rpc.js";
import { api } from "./rpc.js";
import { Panel } from "./components/Panel.js";
import { TitleBar } from "./components/TitleBar.js";
import { StatusBar, type InputMode } from "./components/StatusBar.js";
import type { Hint } from "./components/KeyHint.js";
import { useTheme } from "./ThemeContext.js";
import { Board } from "./views/Board.js";
import { ProjectHome } from "./views/ProjectHome.js";
import { TicketDetail } from "./views/TicketDetail.js";
import { Settings } from "./views/Settings.js";
import { Onboarding } from "./views/Onboarding.js";
import { TextInput } from "./components/TextInput.js";
import { ConfirmDialog } from "./components/ConfirmDialog.js";
import { PtySession } from "./tiling/PtySession.js";
import {
  tilingReducer,
  INITIAL_STATE,
  getLeaves,
  computeRects,
  findNeighbor,
  PTY_TILE_TYPES,
  type Rect,
  type TilingState,
  type TileType,
} from "./tiling/tree.js";

// Ticket split picker key-to-tileType mapping
const TICKET_SPLIT_OPTIONS_MAP: Record<string, TileType> = {
  s: "supervisor",
  h: "ssh",
  i: "info",
  l: "log",
  a: "artifacts",
  d: "diff",
};

// --- Types ---

type Overlay = "settings" | null;

type ContentView =
  | { type: "home" }
  | { type: "board" }
  | { type: "ticket"; ticket: Ticket };

type ActiveTicket = Ticket & { project_label: string };

type SandboxPicker = {
  selectedIndex: number;
  inputMode: "image" | "dockerfile" | null;
  inputValue: string;
  current: SandboxConfig | null;
};

const SANDBOX_OPTIONS = [
  { label: "Set Docker image", key: "image" as const },
  { label: "Set Dockerfile path", key: "dockerfile" as const },
  { label: "Clear (run locally)", key: "clear" as const },
];

type ProjectAction = {
  label: string;
  key: string;
  action: () => void;
};

type ProjectActionsMenu = {
  selectedIndex: number;
  actions: ProjectAction[];
};

// --- Helpers ---

const SIDEBAR_WIDTH = 28;

function padRow(s: string, width: number = SIDEBAR_WIDTH - 2): string {
  return s.length >= width ? s.slice(0, width) : s + " ".repeat(width - s.length);
}

function clamp(v: number, max: number): number {
  return max <= 0 ? 0 : Math.max(0, Math.min(v, max - 1));
}

function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + "\u2026";
}

function formatTimeAgo(dateStr: string): string {
  const ms = Date.now() - new Date(dateStr).getTime();
  if (ms < 0) return "now";
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

// --- Unified region navigation ---

function computeUnifiedRects(
  sidebarW: number,
  totalCols: number,
  totalH: number,
  tilingRoot: ReturnType<typeof getLeaves> extends (infer _)[] ? any : any,
  contentView: ContentView,
  ticketTilingRoot?: ReturnType<typeof getLeaves> extends (infer _)[] ? any : any,
): Map<string, Rect> {
  const rects = new Map<string, Rect>();
  const contentX = sidebarW;
  const contentW = Math.max(10, totalCols - sidebarW);

  // Sidebar: 3-way split
  const projH = Math.floor(totalH * 0.4);
  const supH = Math.floor(totalH * 0.35);
  const contH = totalH - projH - supH;
  rects.set("projects", { x: 0, y: 0, w: sidebarW, h: projH });
  rects.set("supervisors", { x: 0, y: projH, w: sidebarW, h: supH });
  rects.set("containers", { x: 0, y: projH + supH, w: sidebarW, h: contH });

  // Content area — tiles for home or ticket views
  if (contentView.type === "home" && tilingRoot) {
    const tileRects = computeRects(tilingRoot, contentX, 0, contentW, totalH);
    for (const [id, r] of tileRects) {
      rects.set(`tile:${id}`, r);
    }
  } else if (contentView.type === "ticket" && ticketTilingRoot) {
    const tileRects = computeRects(ticketTilingRoot, contentX, 0, contentW, totalH);
    for (const [id, r] of tileRects) {
      rects.set(`tile:${id}`, r);
    }
  } else {
    rects.set("content", { x: contentX, y: 0, w: contentW, h: totalH });
  }

  return rects;
}

// --- Main App ---

export function App() {
  const th = useTheme();
  const { exit } = useApp();
  const { stdout } = useStdout();
  const { stdin, setRawMode } = useStdin();
  const totalCols = stdout?.columns ?? 80;
  const totalRows = stdout?.rows ?? 24;
  const availH = Math.max(5, totalRows - 3); // title bar + status lines
  const contentCols = Math.max(20, totalCols - SIDEBAR_WIDTH);
  const contentRows = availH;

  // Core state
  const [needsOnboarding, setNeedsOnboarding] = useState<boolean | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [inputActive, setInputActive] = useState(false);
  const [overlay, setOverlay] = useState<Overlay>(null);

  // Unified focus + mode
  const [focusedRegion, setFocusedRegion] = useState<string>("projects");
  const [mode, setMode] = useState<InputMode>("NORMAL");
  const lastSidebarRegion = useRef<string>("projects");

  // Remember last content view per sidebar section
  const lastProjectsView = useRef<ContentView>({ type: "home" });
  const lastSupervisorsView = useRef<ContentView>({ type: "home" });

  // Track which sidebar panel the user was last on & save/restore content views
  useEffect(() => {
    if (focusedRegion === "projects" || focusedRegion === "supervisors" || focusedRegion === "containers") {
      // Save current content view for the section we're leaving
      if (lastSidebarRegion.current === "projects" && focusedRegion !== "projects") {
        lastProjectsView.current = contentView;
      } else if (lastSidebarRegion.current === "supervisors" && focusedRegion !== "supervisors") {
        lastSupervisorsView.current = contentView;
      }
      // Restore content view for the section we're entering (default to first item)
      if (focusedRegion === "projects" && lastSidebarRegion.current !== "projects") {
        if (lastProjectsView.current.type === "home" && projects[projectListIndex]) {
          setContentView({ type: "home" });
        } else {
          setContentView(lastProjectsView.current);
        }
      } else if (focusedRegion === "supervisors" && lastSidebarRegion.current !== "supervisors") {
        let view = lastSupervisorsView.current;
        if (view.type === "home" && activeTickets.length > 0) {
          const ticket = activeTickets[supervisorIndex] ?? activeTickets[0];
          view = { type: "ticket", ticket };
          lastSupervisorsView.current = view;
        }
        setContentView(view);
        // Sync project index so supervisor PTY sessions resolve correctly
        if (view.type === "ticket") {
          const projIdx = projects.findIndex((p) => p.id === view.ticket.project_id);
          if (projIdx >= 0) setProjectListIndex(projIdx);
        }
      }
      lastSidebarRegion.current = focusedRegion;
    }
  }, [focusedRegion]);

  // Navigation
  const [projectListIndex, setProjectListIndex] = useState(0);
  const [supervisorIndex, setSupervisorIndex] = useState(0);
  const [containerIndex, setContainerIndex] = useState(0);
  const [contentView, setContentView] = useState<ContentView>({ type: "home" });

  // Data
  const [sessions, setSessions] = useState<OmniSession[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [autoDispatchMap, setAutoDispatchMap] = useState<Record<string, boolean>>({});
  const [activeTickets, setActiveTickets] = useState<ActiveTicket[]>([]);
  const [containers, setContainers] = useState<Container[]>([]);
  const [hotkeys, setHotkeys] = useState<Record<string, string>>({});

  // Derived
  const selectedProject = projects[projectListIndex] ?? null;

  // Clamp indices
  useEffect(() => {
    setProjectListIndex((v) => clamp(v, projects.length));
  }, [projects.length]);
  useEffect(() => {
    setSupervisorIndex((v) => clamp(v, activeTickets.length));
  }, [activeTickets.length]);

  // Tiling state (hoisted from ProjectHome)
  const [tilingState, tilingDispatch] = useReducer(tilingReducer, INITIAL_STATE);
  const tilingStateRef = useRef(tilingState);
  tilingStateRef.current = tilingState;
  const [pickerOpen, setPickerOpen] = useState(false);
  const sessionsRef = useRef<Map<string, PtySession>>(new Map());
  const [renderTick, setRenderTick] = useState(0);
  const forceRender = useCallback(() => setRenderTick((v) => v + 1), []);

  // Ticket tiling state (parallel to project tiling, for ticket detail view)
  const [ticketTilingState, ticketTilingDispatch] = useReducer(tilingReducer, INITIAL_STATE);
  const ticketTilingStateRef = useRef(ticketTilingState);
  ticketTilingStateRef.current = ticketTilingState;
  const [ticketPickerOpen, setTicketPickerOpen] = useState(false);
  const ticketSessionsRef = useRef<Map<string, PtySession>>(new Map());
  const ticketTilingCache = useRef<Map<string, { tilingState: TilingState; sessions: Map<string, PtySession> }>>(new Map());

  // Project hotkey assignment: user is picking a key to bind
  const [assigningHotkey, setAssigningHotkey] = useState(false);
  const assignTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Add/delete project
  const [addingProject, setAddingProject] = useState(false);
  const [addPath, setAddPath] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<Project | null>(null);

  // Project actions menu + sandbox picker
  const [projectActions, setProjectActions] = useState<ProjectActionsMenu | null>(null);
  const [sandboxPicker, setSandboxPicker] = useState<SandboxPicker | null>(null);

  // Sub-view hints/status (for Board/TicketDetail)
  const [subViewHints, setSubViewHints] = useState<Hint[]>([]);
  const [subViewStatus, setSubViewStatus] = useState<string | null>(null);

  // --- Per-project tiling state cache (persists splits across project switching) ---
  const projectTilingCache = useRef<Map<string, { tilingState: TilingState; sessions: Map<string, PtySession> }>>(new Map());

  // --- Project change: save current state, restore or reset for new project ---
  const prevProjectId = useRef<string | null>(null);
  useEffect(() => {
    if (selectedProject?.id !== prevProjectId.current) {
      const currentState = tilingStateRef.current;

      // Save current project's tiling state + sessions
      if (prevProjectId.current !== null && currentState.root) {
        projectTilingCache.current.set(prevProjectId.current, {
          tilingState: { ...currentState },
          sessions: new Map(sessionsRef.current),
        });
        // Detach sessions without killing them
        sessionsRef.current.clear();
      } else if (prevProjectId.current !== null) {
        // No tiling root — clean up any cached entry for this project
        const cached = projectTilingCache.current.get(prevProjectId.current);
        if (cached) {
          for (const s of cached.sessions.values()) s.kill();
          projectTilingCache.current.delete(prevProjectId.current);
        }
      }

      // Restore or reset for the new project
      const newId = selectedProject?.id ?? null;
      const cached = newId ? projectTilingCache.current.get(newId) : null;
      if (cached) {
        tilingDispatch({ type: "RESTORE", state: cached.tilingState });
        sessionsRef.current = new Map(cached.sessions);
        // Re-attach onUpdate callbacks so renders still trigger
        for (const session of sessionsRef.current.values()) {
          session.onUpdate = forceRender;
        }
        projectTilingCache.current.delete(newId!);
      } else {
        for (const s of sessionsRef.current.values()) s.kill();
        sessionsRef.current.clear();
        tilingDispatch({ type: "RESET" });
        // Insert a picker tile so the empty state is a focusable tile
        if (newId) {
          tilingDispatch({ type: "SPLIT", tileType: "picker", command: [] });
        }
      }

      setPickerOpen(false);
      setMode("NORMAL");
      if (prevProjectId.current !== null && focusedRegion === "projects") {
        setFocusedRegion("projects");
      }
      prevProjectId.current = newId;
    }
  }, [selectedProject?.id, forceRender]);

  // --- PTY session lifecycle ---
  useEffect(() => {
    if (!tilingState.root) return;
    const leaves = getLeaves(tilingState.root);
    for (const leaf of leaves) {
      if (!PTY_TILE_TYPES.has(leaf.tileType)) continue;
      if (!sessionsRef.current.has(leaf.id)) {
        const session = new PtySession(leaf.command, {
          cols: Math.max(10, Math.floor(contentCols / 2)),
          rows: Math.max(5, contentRows - 3),
          cwd: leaf.cwd,
          onUpdate: forceRender,
          onExit: () => tilingDispatch({ type: "CLOSE_TILE", id: leaf.id }),
        });
        sessionsRef.current.set(leaf.id, session);
      }
    }
    // Clean up removed leaves
    const leafIds = new Set(leaves.map((l) => l.id));
    for (const [id, session] of sessionsRef.current) {
      if (!leafIds.has(id)) {
        session.kill();
        sessionsRef.current.delete(id);
      }
    }
  }, [tilingState.root, contentCols, contentRows, forceRender]);

  // Clean up all sessions on unmount (including cached ones)
  useEffect(() => {
    return () => {
      for (const session of sessionsRef.current.values()) session.kill();
      sessionsRef.current.clear();
      for (const cached of projectTilingCache.current.values()) {
        for (const s of cached.sessions.values()) s.kill();
      }
      projectTilingCache.current.clear();
    };
  }, []);

  // When tiling tree becomes empty, re-insert a picker tile
  useEffect(() => {
    if (!tilingState.root && contentView.type === "home" && selectedProject) {
      tilingDispatch({ type: "SPLIT", tileType: "picker", command: [] });
      setMode("NORMAL");
    }
  }, [tilingState.root, contentView.type, selectedProject]);

  // --- Ticket tiling: save/restore on ticket switch ---
  const prevTicketId = useRef<string | null>(null);
  useEffect(() => {
    const currentTicketId = contentView.type === "ticket" ? contentView.ticket.id : null;
    if (currentTicketId === prevTicketId.current) return;

    // Save current ticket's state
    if (prevTicketId.current !== null && ticketTilingStateRef.current.root) {
      ticketTilingCache.current.set(prevTicketId.current, {
        tilingState: { ...ticketTilingStateRef.current },
        sessions: new Map(ticketSessionsRef.current),
      });
      ticketSessionsRef.current.clear();
    } else if (prevTicketId.current !== null) {
      const cached = ticketTilingCache.current.get(prevTicketId.current);
      if (cached) {
        for (const s of cached.sessions.values()) s.kill();
        ticketTilingCache.current.delete(prevTicketId.current);
      }
    }

    // Restore or create default for new ticket
    if (currentTicketId) {
      const cached = ticketTilingCache.current.get(currentTicketId);
      if (cached) {
        ticketTilingDispatch({ type: "RESTORE", state: cached.tilingState });
        ticketSessionsRef.current = new Map(cached.sessions);
        for (const session of ticketSessionsRef.current.values()) {
          session.onUpdate = forceRender;
        }
        ticketTilingCache.current.delete(currentTicketId);
      } else {
        // Kill any orphaned sessions
        for (const s of ticketSessionsRef.current.values()) s.kill();
        ticketSessionsRef.current.clear();
        ticketTilingDispatch({ type: "RESET" });
        // Create default layout: supervisor (left) + info (right)
        // We dispatch two SPLITs: first creates the supervisor, second splits with info
        ticketTilingDispatch({
          type: "SPLIT",
          tileType: "info",
          command: [],
          ticketId: currentTicketId,
        });
        // Resolve supervisor command and add it
        if (selectedProject) {
          api.terminalResolveChat(currentTicketId, selectedProject.id)
            .then((r) => {
              ticketTilingDispatch({
                type: "SPLIT",
                tileType: "supervisor",
                command: r.command,
                cwd: r.cwd,
                ticketId: currentTicketId,
              });
              // Allow the auto-focus effect to re-trigger for this ticket
              // so focusedRegion syncs to the new supervisor tile.
              ticketAutoFocused.current = null;
            })
            .catch(() => {
              // No supervisor available — that's OK, info-only layout
            });
        }
      }
    } else {
      // Leaving ticket view
      for (const s of ticketSessionsRef.current.values()) s.kill();
      ticketSessionsRef.current.clear();
      ticketTilingDispatch({ type: "RESET" });
    }

    setTicketPickerOpen(false);
    prevTicketId.current = currentTicketId;
  }, [contentView, selectedProject, forceRender]);

  // --- Ticket PTY session lifecycle ---
  useEffect(() => {
    if (contentView.type !== "ticket") return;
    if (!ticketTilingState.root) return;
    const leaves = getLeaves(ticketTilingState.root);
    for (const leaf of leaves) {
      if (!PTY_TILE_TYPES.has(leaf.tileType)) continue; // Content tiles don't need PTY
      if (!ticketSessionsRef.current.has(leaf.id) && leaf.command.length > 0) {
        const session = new PtySession(leaf.command, {
          cols: Math.max(10, Math.floor(contentCols / 2)),
          rows: Math.max(5, contentRows - 3),
          cwd: leaf.cwd,
          onUpdate: forceRender,
          onExit: () => ticketTilingDispatch({ type: "CLOSE_TILE", id: leaf.id }),
        });
        ticketSessionsRef.current.set(leaf.id, session);
      }
    }
    // Clean up removed PTY leaves
    const leafIds = new Set(leaves.map((l) => l.id));
    for (const [id, session] of ticketSessionsRef.current) {
      if (!leafIds.has(id)) {
        session.kill();
        ticketSessionsRef.current.delete(id);
      }
    }
  }, [ticketTilingState.root, contentView.type, contentCols, contentRows, forceRender]);

  // When ticket tiling tree becomes empty, return focus to sidebar
  useEffect(() => {
    if (!ticketTilingState.root && focusedRegion.startsWith("tile:") && contentView.type === "ticket") {
      setFocusedRegion(lastSidebarRegion.current);
      setMode("NORMAL");
    }
  }, [ticketTilingState.root, focusedRegion, contentView.type]);

  // Auto-focus first ticket tile when entering ticket view (one-shot per ticket)
  // Only auto-focus if the user isn't browsing from the sidebar
  const ticketAutoFocused = useRef<string | null>(null);
  useEffect(() => {
    const ticketId = contentView.type === "ticket" ? contentView.ticket.id : null;
    if (!ticketId) { ticketAutoFocused.current = null; return; }
    if (ticketAutoFocused.current === ticketId) return; // already auto-focused this ticket
    if (!ticketTilingState.root || !ticketTilingState.focusedId) return;
    // Don't steal focus from sidebar — only auto-focus if already on tiles/content
    if (focusedRegion === "projects" || focusedRegion === "supervisors" || focusedRegion === "containers") return;
    ticketAutoFocused.current = ticketId;
    setFocusedRegion(`tile:${ticketTilingState.focusedId}`);
  }, [contentView, ticketTilingState.root, ticketTilingState.focusedId, focusedRegion]);

  // Clean up ticket sessions on unmount
  useEffect(() => {
    return () => {
      for (const session of ticketSessionsRef.current.values()) session.kill();
      ticketSessionsRef.current.clear();
      for (const cached of ticketTilingCache.current.values()) {
        for (const s of cached.sessions.values()) s.kill();
      }
      ticketTilingCache.current.clear();
    };
  }, []);

  // Init
  useEffect(() => {
    api.onboardingStatus()
      .then((s) => setNeedsOnboarding(!s.models_configured))
      .catch(() => setNeedsOnboarding(false));
  }, []);

  // Refresh hotkeys
  const refreshHotkeys = useCallback(async () => {
    try {
      const map = await api.getProjectHotkeys();
      setHotkeys(map);
    } catch {}
  }, []);

  // Refresh sessions
  const refreshSessions = useCallback(async () => {
    try {
      const list = await api.listSessions(50, 0);
      setSessions(list);
    } catch {}
  }, []);

  // Refresh projects + supervisors
  const refreshProjects = useCallback(async () => {
    try {
      const projectList = await api.listProjects();
      setProjects(projectList);

      const adMap: Record<string, boolean> = {};
      const allActive: ActiveTicket[] = [];
      for (const p of projectList) {
        try {
          const r = await api.getAutoDispatch(p.id);
          adMap[p.id] = r.enabled;
        } catch {
          adMap[p.id] = false;
        }
        try {
          const tickets = await api.listTickets(p.id);
          for (const t of tickets) {
            if (t.supervisor_session_id || (t.task && t.task.status?.type === "running")) {
              allActive.push({ ...t, project_label: p.label });
            }
          }
        } catch {}
      }
      setAutoDispatchMap(adMap);
      allActive.sort((a, b) => b.updated_at - a.updated_at);
      setActiveTickets(allActive);

      try {
        const containerList = await api.listContainers();
        setContainers(containerList);
      } catch {}
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
    refreshProjects();
    refreshHotkeys();
    const interval = setInterval(() => {
      refreshSessions();
      refreshProjects();
    }, 5000);
    return () => clearInterval(interval);
  }, [refreshSessions, refreshProjects, refreshHotkeys]);

  // Status auto-clear
  useEffect(() => {
    if (!status) return;
    const timer = setTimeout(() => setStatus(null), 4000);
    return () => clearTimeout(timer);
  }, [status]);

  // --- Tiling helpers ---

  const doSplit = useCallback(
    (tileType: "chat" | "terminal") => {
      if (!selectedProject) return;
      // Check if the focused tile is a picker — if so, replace in-place
      const pickerLeaf = tilingState.root && tilingState.focusedId
        ? getLeaves(tilingState.root).find((l) => l.id === tilingState.focusedId && l.tileType === "picker")
        : null;
      const resolve = tileType === "chat"
        ? api.projectResolveOmni(selectedProject.id)
        : api.projectResolveShell(selectedProject.id);
      resolve
        .then((resolved) => {
          if (pickerLeaf) {
            tilingDispatch({ type: "REPLACE_TILE", id: pickerLeaf.id, tileType, command: resolved.command, cwd: resolved.cwd ?? selectedProject.workspace_dir });
          } else {
            tilingDispatch({ type: "SPLIT", tileType, command: resolved.command, cwd: resolved.cwd ?? selectedProject.workspace_dir });
            setFocusedRegion(`tile:${String(tilingState.nextId + 1)}`);
          }
          setContentView({ type: "home" });
          setPickerOpen(false);
        })
        .catch((err) => {
          setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
        });
    },
    [selectedProject, tilingState.nextId, tilingState.root, tilingState.focusedId],
  );

  // Ticket tile split helper
  const doTicketSplit = useCallback(
    (tileType: TileType) => {
      if (!selectedProject || contentView.type !== "ticket") return;
      const ticketId = contentView.ticket.id;

      // Check singleton constraint
      if ((tileType === "supervisor" || tileType === "ssh") && ticketTilingState.root) {
        const existing = getLeaves(ticketTilingState.root).find((l) => l.tileType === tileType);
        if (existing) return;
      }

      const newId = String(ticketTilingState.nextId + 1);

      if (PTY_TILE_TYPES.has(tileType)) {
        // PTY tiles need command resolution
        const resolve = tileType === "supervisor"
          ? api.terminalResolveChat(ticketId, selectedProject.id)
          : tileType === "ssh"
            ? api.terminalResolveSsh(ticketId, selectedProject.id)
            : tileType === "chat"
              ? api.terminalResolveChat(ticketId, selectedProject.id)
              : api.terminalResolveSsh(ticketId, selectedProject.id);
        resolve
          .then((r) => {
            ticketTilingDispatch({ type: "SPLIT", tileType, command: r.command, cwd: r.cwd, ticketId });
            setFocusedRegion(`tile:${newId}`);
            setTicketPickerOpen(false);
          })
          .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
      } else {
        // Content tiles don't need command resolution
        ticketTilingDispatch({ type: "SPLIT", tileType, command: [], ticketId });
        setFocusedRegion(`tile:${newId}`);
        setTicketPickerOpen(false);
      }
    },
    [selectedProject, contentView, ticketTilingState.nextId, ticketTilingState.root],
  );

  const closeCurrentTile = useCallback(() => {
    if (!focusedRegion.startsWith("tile:")) return;
    const tileId = focusedRegion.slice(5);
    const isTicket = contentView.type === "ticket";
    const dispatch = isTicket ? ticketTilingDispatch : tilingDispatch;
    const sessions = isTicket ? ticketSessionsRef : sessionsRef;
    const root = isTicket ? ticketTilingState.root : tilingState.root;

    const session = sessions.current.get(tileId);
    if (session) session.kill();
    dispatch({ type: "CLOSE_TILE", id: tileId });
    // Focus moves to next tile or back to sidebar
    if (root) {
      const leaves = getLeaves(root).filter((l) => l.id !== tileId);
      if (leaves.length > 0) {
        setFocusedRegion(`tile:${leaves[0].id}`);
      } else {
        setFocusedRegion(lastSidebarRegion.current);
      }
    } else {
      setFocusedRegion(lastSidebarRegion.current);
    }
    setMode("NORMAL");
  }, [focusedRegion, tilingState.root, ticketTilingState.root, contentView.type]);

  // --- Unified spatial navigation ---

  const navigateDirection = useCallback(
    (dir: "up" | "down" | "left" | "right") => {
      const sidebarRegions = ["projects", "supervisors", "containers"];
      const isSidebar = sidebarRegions.includes(focusedRegion);
      const sidebarIdx = sidebarRegions.indexOf(focusedRegion);

      if (isSidebar) {
        if (dir === "up" && sidebarIdx > 0) {
          setFocusedRegion(sidebarRegions[sidebarIdx - 1]);
          return;
        }
        if (dir === "down" && sidebarIdx < sidebarRegions.length - 1) {
          setFocusedRegion(sidebarRegions[sidebarIdx + 1]);
          return;
        }
        if (dir === "right") {
          // Jump to first tile or content
          const isTicket = contentView.type === "ticket";
          const root = isTicket ? ticketTilingState.root : tilingState.root;
          if (root) {
            const leaves = getLeaves(root);
            if (leaves.length > 0) {
              const id = leaves[0].id;
              setFocusedRegion(`tile:${id}`);
              const dispatch = isTicket ? ticketTilingDispatch : tilingDispatch;
              dispatch({ type: "FOCUS_TILE", id });
              return;
            }
          }
          if (contentView.type !== "home") {
            setFocusedRegion("content");
          }
        }
        return;
      }

      // From tiles/content: left goes to sidebar, up/down/right use spatial nav within tiles
      if (dir === "left" && focusedRegion.startsWith("tile:")) {
        // Check if there's a tile further left first
        const isTicket = contentView.type === "ticket";
        const root = isTicket ? ticketTilingState.root : tilingState.root;
        if (root) {
          const tileRects = computeRects(root, 0, 0, totalCols, availH);
          const neighbor = findNeighbor(tileRects, focusedRegion.slice(5), "left");
          if (neighbor) {
            setFocusedRegion(`tile:${neighbor}`);
            const dispatch = isTicket ? ticketTilingDispatch : tilingDispatch;
            dispatch({ type: "FOCUS_TILE", id: neighbor });
            return;
          }
        }
        // No tile to the left — go to sidebar
        setFocusedRegion(lastSidebarRegion.current);
        return;
      }

      if (dir === "left" && focusedRegion === "content") {
        setFocusedRegion(lastSidebarRegion.current);
        return;
      }

      // Within tiles: use spatial navigation
      if (focusedRegion.startsWith("tile:")) {
        const isTicket = contentView.type === "ticket";
        const root = isTicket ? ticketTilingState.root : tilingState.root;
        if (root) {
          const tileRects = computeRects(root, 0, 0, totalCols, availH);
          const neighbor = findNeighbor(tileRects, focusedRegion.slice(5), dir);
          if (neighbor) {
            setFocusedRegion(`tile:${neighbor}`);
            const dispatch = isTicket ? ticketTilingDispatch : tilingDispatch;
            dispatch({ type: "FOCUS_TILE", id: neighbor });
          }
        }
      }
    },
    [totalCols, availH, tilingState.root, ticketTilingState.root, contentView, focusedRegion],
  );

  // Tile-only navigation (for INSERT mode — stays within tiles)
  const navigateTiles = useCallback(
    (dir: "up" | "down" | "left" | "right") => {
      const isTicket = contentView.type === "ticket";
      const root = isTicket ? ticketTilingState.root : tilingState.root;
      const focusedId = isTicket ? ticketTilingState.focusedId : tilingState.focusedId;
      const dispatch = isTicket ? ticketTilingDispatch : tilingDispatch;
      if (!root || !focusedId) return;
      const tileRects = computeRects(root, 0, 0, contentCols, contentRows);
      const neighbor = findNeighbor(tileRects, focusedId, dir);
      if (neighbor) {
        setFocusedRegion(`tile:${neighbor}`);
        dispatch({ type: "FOCUS_TILE", id: neighbor });
      }
    },
    [tilingState.root, tilingState.focusedId, ticketTilingState.root, ticketTilingState.focusedId, contentView.type, contentCols, contentRows],
  );

  // --- Navigation callbacks ---
  const ctrlCTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [ctrlCPending, setCtrlCPending] = useState(false);
  const handleQuit = useCallback(() => exit(), [exit]);
  /** Release focus from tiles/content back to the sidebar. */
  const handleReleaseFocus = useCallback(() => {
    setFocusedRegion(lastSidebarRegion.current);
    setMode("NORMAL");
  }, []);


  // Add project handler
  const handleAddProject = useCallback(
    async (path: string) => {
      const trimmed = path.trim();
      setAddingProject(false);
      if (!trimmed) return;
      try {
        const parts = trimmed.split("/");
        const label = parts[parts.length - 1] || trimmed;
        await api.addProject(label, trimmed);
        setStatus(`Added project ${label}`);
        refreshProjects();
      } catch (err) {
        setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [refreshProjects],
  );

  // Delete project handler
  const handleDeleteProject = useCallback(() => {
    if (!confirmDelete) return;
    const project = confirmDelete;
    setConfirmDelete(null);
    api
      .removeProject(project.id)
      .then(() => {
        setStatus(`Removed ${project.label}`);
        setContentView({ type: "home" });
        setFocusedRegion("projects");
        refreshProjects();
      })
      .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
  }, [confirmDelete, refreshProjects]);

  const handleCloseOverlay = useCallback(() => setOverlay(null), []);

  // --- Ctrl+J raw stdin handler (works in both NORMAL and INSERT) ---
  // Ctrl+J produces \x0a which Ink misparses as Return, so we must
  // intercept it at the raw stdin level regardless of mode.
  useEffect(() => {
    if (!focusedRegion.startsWith("tile:") || !stdin) return;
    if (mode !== "NORMAL" && mode !== "INSERT") return;

    const dispatch = contentView.type === "ticket" ? ticketTilingDispatch : tilingDispatch;
    const ctrlJListener = (data: string | Buffer) => {
      const str = typeof data === "string" ? data : data.toString("utf-8");
      if (str === "\x0a") {
        dispatch({ type: "ROTATE" });
      }
    };

    // Prepend so it fires before the INSERT listener below
    stdin.prependListener("data", ctrlJListener);
    return () => { stdin.off("data", ctrlJListener); };
  }, [mode, focusedRegion, stdin, contentView.type]);

  // --- INSERT mode: raw stdin handler ---
  useEffect(() => {
    if (mode !== "INSERT" || !focusedRegion.startsWith("tile:") || !stdin) return;

    const isTicket = contentView.type === "ticket";
    const dispatch = isTicket ? ticketTilingDispatch : tilingDispatch;
    const sessions = isTicket ? ticketSessionsRef : sessionsRef;
    const openPicker = isTicket ? setTicketPickerOpen : setPickerOpen;

    setRawMode(true);

    const listener = (data: string | Buffer) => {
      const str = typeof data === "string" ? data : data.toString("utf-8");

      // Escape (\x1b) or Ctrl+] (\x1d) — release to NORMAL
      if (str === "\x1b" || str === "\x1d") {
        setMode("NORMAL");
        return;
      }

      // Ctrl+n (\x0e) — open split picker
      if (str === "\x0e") {
        setMode("NORMAL");
        openPicker(true);
        return;
      }

      // Ctrl+w (\x17) — close focused tile
      if (str === "\x17") {
        closeCurrentTile();
        return;
      }

      // Ctrl+arrows: navigate tiles only (stay in INSERT)
      if (str === "\x1b[1;5D") { navigateTiles("left"); return; }
      if (str === "\x1b[1;5C") { navigateTiles("right"); return; }
      if (str === "\x1b[1;5A") { navigateTiles("up"); return; }
      if (str === "\x1b[1;5B") { navigateTiles("down"); return; }

      // Ctrl+Shift+arrows: swap panes
      if (str === "\x1b[1;6D") { dispatch({ type: "SWAP", direction: "left", availCols: contentCols, availRows: contentRows }); return; }
      if (str === "\x1b[1;6C") { dispatch({ type: "SWAP", direction: "right", availCols: contentCols, availRows: contentRows }); return; }
      if (str === "\x1b[1;6A") { dispatch({ type: "SWAP", direction: "up", availCols: contentCols, availRows: contentRows }); return; }
      if (str === "\x1b[1;6B") { dispatch({ type: "SWAP", direction: "down", availCols: contentCols, availRows: contentRows }); return; }

      // Ctrl+- (\x1f) — shrink horizontal
      if (str === "\x1f") {
        dispatch({ type: "RESIZE", axis: "horizontal", delta: -1, availCols: contentCols, availRows: contentRows });
        return;
      }

      // Ctrl+= (kitty: \x1b[61;5u) — grow horizontal
      if (str === "\x1b[61;5u") {
        dispatch({ type: "RESIZE", axis: "horizontal", delta: 1, availCols: contentCols, availRows: contentRows });
        return;
      }

      // Ctrl+j (\x0a) — handled by shared listener above
      if (str === "\x0a") {
        return;
      }

      // Forward everything else to the focused PTY
      const tileId = focusedRegion.slice(5);
      const session = sessions.current.get(tileId);
      if (session) session.write(str);
    };

    stdin.on("data", listener);
    return () => {
      stdin.off("data", listener);
      try { setRawMode(false); } catch {}
    };
  }, [mode, focusedRegion, stdin, setRawMode, contentCols, contentRows, closeCurrentTile, navigateTiles, contentView.type]);

  // --- Reserved Ctrl+key combos that cannot be used as project hotkeys ---
  const RESERVED_CTRL = new Set(["c", "n", "j"]);

  // --- Project hotkey: Ctrl+<key> to jump, Ctrl+Shift+<key> to assign ---
  // Handled in useInput below. Since most terminals can't distinguish Ctrl+Shift+letter
  // from Ctrl+letter at the raw byte level, assignment uses a two-step flow:
  // press "H" (capital) to enter assign mode, then press any key to bind it.

  // --- NORMAL mode input handling ---
  useInput((input: string, key: Key) => {
    // Double Ctrl+C to quit — must be first to prevent "c" from triggering other shortcuts
    if (key.ctrl && input === "c") {
      if (ctrlCPending) {
        if (ctrlCTimerRef.current) clearTimeout(ctrlCTimerRef.current);
        exit();
      } else {
        setCtrlCPending(true);
        setStatus("Press Ctrl+C again to quit");
        if (ctrlCTimerRef.current) clearTimeout(ctrlCTimerRef.current);
        ctrlCTimerRef.current = setTimeout(() => setCtrlCPending(false), 2000);
      }
      return;
    }

    if (needsOnboarding) return;
    if (inputActive || addingProject || confirmDelete) return;
    if (mode === "INSERT") return;
    if (overlay) return;

    // --- Project actions menu ---
    if (projectActions) {
      if (key.escape) { setProjectActions(null); return; }
      if (key.upArrow || input === "k") {
        setProjectActions((prev) => prev ? { ...prev, selectedIndex: clamp(prev.selectedIndex - 1, prev.actions.length) } : null);
        return;
      }
      if (key.downArrow || input === "j") {
        setProjectActions((prev) => prev ? { ...prev, selectedIndex: clamp(prev.selectedIndex + 1, prev.actions.length) } : null);
        return;
      }
      if (key.return) {
        const action = projectActions.actions[projectActions.selectedIndex];
        if (action) {
          setProjectActions(null);
          action.action();
        }
        return;
      }
      return;
    }

    // --- Sandbox picker mode ---
    if (sandboxPicker) {
      if (sandboxPicker.inputMode) {
        if (key.escape) {
          setSandboxPicker((prev) => prev ? { ...prev, inputMode: null, inputValue: "" } : null);
          return;
        }
        if (key.return) {
          const value = sandboxPicker.inputValue.trim();
          if (value && selectedProject) {
            const config: SandboxConfig = sandboxPicker.inputMode === "image"
              ? { image: value }
              : { dockerfile: value };
            api.setSandbox(selectedProject.id, config)
              .then(() => { setStatus(`Sandbox set to ${sandboxPicker.inputMode}: ${value}`); setSandboxPicker(null); refreshProjects(); })
              .catch((err) => { setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`); setSandboxPicker(null); });
          } else {
            setSandboxPicker((prev) => prev ? { ...prev, inputMode: null, inputValue: "" } : null);
          }
          return;
        }
        if (key.backspace || key.delete) {
          setSandboxPicker((prev) => prev ? { ...prev, inputValue: prev.inputValue.slice(0, -1) } : null);
          return;
        }
        if (!key.ctrl && !key.meta && input) {
          setSandboxPicker((prev) => prev ? { ...prev, inputValue: prev.inputValue + input } : null);
        }
        return;
      }
      if (key.escape) { setSandboxPicker(null); return; }
      if (key.upArrow || input === "k") {
        setSandboxPicker((prev) => prev ? { ...prev, selectedIndex: clamp(prev.selectedIndex - 1, SANDBOX_OPTIONS.length) } : null);
        return;
      }
      if (key.downArrow || input === "j") {
        setSandboxPicker((prev) => prev ? { ...prev, selectedIndex: clamp(prev.selectedIndex + 1, SANDBOX_OPTIONS.length) } : null);
        return;
      }
      if (key.return) {
        const option = SANDBOX_OPTIONS[sandboxPicker.selectedIndex];
        if (option.key === "clear" && selectedProject) {
          api.setSandbox(selectedProject.id, null)
            .then(() => { setStatus("Sandbox cleared (running locally)"); setSandboxPicker(null); refreshProjects(); })
            .catch((err) => { setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`); setSandboxPicker(null); });
        } else if (option.key === "image" || option.key === "dockerfile") {
          const prefill = option.key === "image"
            ? (sandboxPicker.current?.image || "")
            : (sandboxPicker.current?.dockerfile || "");
          setSandboxPicker((prev) => prev ? { ...prev, inputMode: option.key, inputValue: prefill } : null);
        }
        return;
      }
      return;
    }

    // --- Hotkey assignment mode: waiting for user to press a key ---
    if (assigningHotkey) {
      if (assignTimerRef.current) { clearTimeout(assignTimerRef.current); assignTimerRef.current = null; }
      setAssigningHotkey(false);
      if (key.escape) { setStatus(null); return; }
      const hk = input.toLowerCase();
      if (hk.length === 1 && selectedProject) {
        if (RESERVED_CTRL.has(hk)) {
          setStatus(`Ctrl+${hk} is reserved`);
        } else {
          api.setProjectHotkey(hk, selectedProject.id)
            .then(() => {
              refreshHotkeys();
              setStatus(`Assigned C-${hk} → ${selectedProject.label}`);
            })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
        }
      }
      return;
    }

    // --- Ctrl+<key> to jump to project ---
    if (key.ctrl && !key.meta && input.length === 1 && !RESERVED_CTRL.has(input)) {
      const projectId = hotkeys[input];
      if (projectId) {
        const idx = projects.findIndex((p) => p.id === projectId);
        if (idx >= 0) {
          setProjectListIndex(idx);
          setFocusedRegion("projects");
          if (contentView.type !== "home") setContentView({ type: "home" });
          setStatus(`Switched to ${projects[idx]!.label}`);
        }
        return;
      }
    }

    // --- "H" to enter hotkey assign mode ---
    if (input === "H" && !key.ctrl && !key.meta && focusedRegion === "projects") {
      if (selectedProject) {
        setAssigningHotkey(true);
        if (assignTimerRef.current) clearTimeout(assignTimerRef.current);
        assignTimerRef.current = setTimeout(() => { setAssigningHotkey(false); setStatus(null); }, 3000);
        setStatus(`Assign hotkey for ${selectedProject.label} — press any key (Esc to cancel)`);
      }
      return;
    }

    // Global overlays
    if (input === "S" && !key.ctrl && !key.meta) { setOverlay("settings"); return; }
    if (input === "C" && !key.ctrl && !key.meta) { setFocusedRegion("containers"); return; }

    // Ctrl+arrows: unified spatial navigation
    if (key.ctrl && key.leftArrow) { navigateDirection("left"); return; }
    if (key.ctrl && key.rightArrow) { navigateDirection("right"); return; }
    if (key.ctrl && key.upArrow) { navigateDirection("up"); return; }
    if (key.ctrl && key.downArrow) { navigateDirection("down"); return; }

    // Tab cycles through all regions
    if (key.tab) {
      const regionOrder: string[] = ["projects", "supervisors", "containers"];
      if (contentView.type === "home" && tilingState.root) {
        const leaves = getLeaves(tilingState.root);
        for (const leaf of leaves) regionOrder.push(`tile:${leaf.id}`);
      } else if (contentView.type === "ticket" && ticketTilingState.root) {
        const leaves = getLeaves(ticketTilingState.root);
        for (const leaf of leaves) regionOrder.push(`tile:${leaf.id}`);
      } else if (contentView.type !== "home") {
        regionOrder.push("content");
      }
      const idx = regionOrder.indexOf(focusedRegion);
      const next = regionOrder[(idx + 1) % regionOrder.length];
      setFocusedRegion(next);
      if (next.startsWith("tile:")) {
        const dispatch = contentView.type === "ticket" ? ticketTilingDispatch : tilingDispatch;
        dispatch({ type: "FOCUS_TILE", id: next.slice(5) });
      }
      return;
    }

    // --- Split picker mode (project) ---
    if (pickerOpen) {
      if (input === "c") { doSplit("chat"); return; }
      if (input === "t") { doSplit("terminal"); return; }
      if (key.escape) {
        setPickerOpen(false);
        if (!tilingState.root) setFocusedRegion(lastSidebarRegion.current);
        return;
      }
      return;
    }

    // --- Split picker mode (ticket) ---
    if (ticketPickerOpen) {
      const opt = TICKET_SPLIT_OPTIONS_MAP[input];
      if (opt) { doTicketSplit(opt); return; }
      if (key.escape) {
        setTicketPickerOpen(false);
        if (!ticketTilingState.root) setFocusedRegion(lastSidebarRegion.current);
        return;
      }
      return;
    }

    // Enter: if on a tile, enter INSERT mode (only for PTY tiles)
    if (key.return && focusedRegion.startsWith("tile:")) {
      // Check if the focused tile is a PTY tile
      const isTicket = contentView.type === "ticket";
      const root = isTicket ? ticketTilingState.root : tilingState.root;
      const focusedId = isTicket ? ticketTilingState.focusedId : tilingState.focusedId;
      if (root && focusedId) {
        const leaves = getLeaves(root);
        const leaf = leaves.find((l) => l.id === focusedId);
        if (leaf && PTY_TILE_TYPES.has(leaf.tileType)) {
          setMode("INSERT");
        }
        // Content tiles: stay in NORMAL, tile handles its own keys
      }
      return;
    }

    // Enter: if on content (board/ticket), let child handle it
    // (handled by passing active=true to child components)

    // --- Projects section ---
    if (focusedRegion === "projects") {
      const totalRows = projects.length;
      if ((key.upArrow && !key.ctrl) || input === "k") {
        setProjectListIndex((prev) => {
          const next = clamp(prev - 1, totalRows);
          if (next !== prev && contentView.type !== "home") {
            setContentView({ type: "home" });
          }
          return next;
        });
        return;
      }
      if ((key.downArrow && !key.ctrl) || input === "j") {
        setProjectListIndex((prev) => {
          const next = clamp(prev + 1, totalRows);
          if (next !== prev && contentView.type !== "home") {
            setContentView({ type: "home" });
          }
          return next;
        });
        return;
      }

      if (!selectedProject) {
        if (input === "a") { setAddingProject(true); setAddPath(""); return; }
      } else {
        if (input === "b") { setContentView({ type: "board" }); setFocusedRegion("content"); return; }
        if (input === "a") { setAddingProject(true); setAddPath(""); return; }
        if (input === "d") { setConfirmDelete(selectedProject); return; }
        if (input === " ") {
          const project = selectedProject;
          setProjectActions({
            selectedIndex: 0,
            actions: [
              {
                label: "Configure sandbox",
                key: "sandbox",
                action: () => {
                  api.getSandbox(project.id)
                    .then((r) => setSandboxPicker({ selectedIndex: 0, inputMode: null, inputValue: "", current: r.sandbox }))
                    .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
                },
              },
              ...(project.sandbox ? [{
                label: "Rebuild sandbox image",
                key: "rebuild",
                action: () => {
                  setStatus(`Rebuilding sandbox image for ${project.label}...`);
                  api.sandboxRebuildImage(project.id)
                    .then((result) => {
                      setStatus(result.note ?? `Image rebuilt: ${result.image}`);
                      refreshProjects();
                    })
                    .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
                },
              }] : []),
            ],
          });
          return;
        }
      }
    }

    // --- Supervisors section ---
    if (focusedRegion === "supervisors") {
      if ((key.upArrow && !key.ctrl) || input === "k") {
        setSupervisorIndex((v) => {
          const next = clamp(v - 1, activeTickets.length);
          const ticket = activeTickets[next];
          if (ticket) {
            const projIdx = projects.findIndex((p) => p.id === ticket.project_id);
            if (projIdx >= 0) setProjectListIndex(projIdx);
            setContentView({ type: "ticket", ticket });
          }
          return next;
        });
        return;
      }
      if ((key.downArrow && !key.ctrl) || input === "j") {
        setSupervisorIndex((v) => {
          const next = clamp(v + 1, activeTickets.length);
          const ticket = activeTickets[next];
          if (ticket) {
            const projIdx = projects.findIndex((p) => p.id === ticket.project_id);
            if (projIdx >= 0) setProjectListIndex(projIdx);
            setContentView({ type: "ticket", ticket });
          }
          return next;
        });
        return;
      }
    }

    // --- Containers section ---
    if (focusedRegion === "containers") {
      if ((key.upArrow && !key.ctrl) || input === "k") { setContainerIndex((v) => clamp(v - 1, containers.length)); return; }
      if ((key.downArrow && !key.ctrl) || input === "j") { setContainerIndex((v) => clamp(v + 1, containers.length)); return; }
      const currentContainer = containers[containerIndex];
      if (currentContainer) {
        if (input === "s") {
          setStatus(`Starting ${currentContainer.name}...`);
          api.startContainer(currentContainer.name)
            .then(() => { setStatus(`Started ${currentContainer.name}`); refreshProjects(); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
          return;
        }
        if (input === "x") {
          setStatus(`Stopping ${currentContainer.name}...`);
          api.stopContainer(currentContainer.name)
            .then(() => { setStatus(`Stopped ${currentContainer.name}`); refreshProjects(); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
          return;
        }
        if (input === "R") {
          setStatus(`Removing ${currentContainer.name}...`);
          api.rebuildContainer(currentContainer.name)
            .then(() => { setStatus(`Removed ${currentContainer.name}`); refreshProjects(); })
            .catch((err) => setStatus(`Error: ${err instanceof Error ? err.message : String(err)}`));
          return;
        }
      }
    }

    // --- Tile NORMAL mode ---
    if (focusedRegion.startsWith("tile:")) {
      const isTicket = contentView.type === "ticket";
      const dispatch = isTicket ? ticketTilingDispatch : tilingDispatch;
      const openPicker = isTicket ? setTicketPickerOpen : setPickerOpen;
      const root = isTicket ? ticketTilingState.root : tilingState.root;
      const focusedTileId = isTicket ? ticketTilingState.focusedId : tilingState.focusedId;

      // Picker tile: c/t to open, Esc to go back to sidebar
      if (root && focusedTileId) {
        const focusedLeaf = getLeaves(root).find((l) => l.id === focusedTileId);
        if (focusedLeaf?.tileType === "picker") {
          if (input === "c") { doSplit("chat"); return; }
          if (input === "t") { doSplit("terminal"); return; }
          if (key.escape) { setFocusedRegion(lastSidebarRegion.current); return; }
          return; // No other keys on picker tile
        }
      }

      if (key.ctrl && input === "n") { openPicker(true); return; }
      if (input === "x") { closeCurrentTile(); return; }

      // Resize in NORMAL mode
      if (input === "-") { dispatch({ type: "RESIZE", axis: "horizontal", delta: -1, availCols: contentCols, availRows: contentRows }); return; }
      if (input === "=") { dispatch({ type: "RESIZE", axis: "horizontal", delta: 1, availCols: contentCols, availRows: contentRows }); return; }
      if (input === "_") { dispatch({ type: "RESIZE", axis: "vertical", delta: -1, availCols: contentCols, availRows: contentRows }); return; }
      if (input === "+") { dispatch({ type: "RESIZE", axis: "vertical", delta: 1, availCols: contentCols, availRows: contentRows }); return; }
      // Ctrl+j rotate is handled by the raw stdin listener (Ink misparses \x0a as Return)
    }

    // --- Content region (board/ticket) ---
    // These views handle their own input via active prop

    // Global shortcuts
    if (input === "r") { refreshSessions(); refreshProjects(); refreshHotkeys(); setStatus("Refreshed"); return; }
  });

  // --- Breadcrumbs ---
  const breadcrumbs: string[] = [];
  if (overlay) {
    breadcrumbs.push("Settings");
  } else {
    breadcrumbs.push("Projects");
    if (selectedProject) {
      breadcrumbs.push(selectedProject.label);
      if (contentView.type === "board") {
        breadcrumbs.push("Board");
      } else if (contentView.type === "ticket") {
        breadcrumbs.push("Board");
        breadcrumbs.push(truncate(contentView.ticket.title, 30));
      }
    }
  }

  // --- Hints ---
  const getHints = (): Hint[] => {
    if (mode === "INSERT") {
      return [
        { key: "esc", label: "release" },
        { key: "C-\u2190\u2191\u2192\u2193", label: "navigate" },
        { key: "C-n", label: "new split" },
        { key: "C-w", label: "close" },
      ];
    }

    if (pickerOpen) {
      return [
        { key: "c", label: "chat" },
        { key: "t", label: "terminal" },
        { key: "esc", label: "cancel" },
      ];
    }

    if (ticketPickerOpen) {
      return [
        { key: "s", label: "supervisor" },
        { key: "h", label: "ssh" },
        { key: "i", label: "info" },
        { key: "l", label: "log" },
        { key: "a", label: "artifacts" },
        { key: "d", label: "diff" },
        { key: "esc", label: "cancel" },
      ];
    }

    if (focusedRegion === "content") return subViewHints;

    if (focusedRegion.startsWith("tile:")) {
      // Check if focused tile is a picker
      const isTicket = contentView.type === "ticket";
      const root = isTicket ? ticketTilingState.root : tilingState.root;
      const focusedTileId = isTicket ? ticketTilingState.focusedId : tilingState.focusedId;
      if (root && focusedTileId) {
        const focusedLeaf = getLeaves(root).find((l) => l.id === focusedTileId);
        if (focusedLeaf?.tileType === "picker") {
          return [
            { key: "c", label: "chat" },
            { key: "t", label: "terminal" },
            { key: "esc", label: "back" },
          ];
        }
      }
      return [
        { key: "enter", label: "focus PTY" },
        { key: "C-\u2190\u2191\u2192\u2193", label: "navigate" },
        { key: "tab", label: "cycle" },
        { key: "C-n", label: "new split" },
        { key: "-/=", label: "resize" },
        { key: "x", label: "close" },
      ];
    }

    const base: Hint[] = [
      { key: "j/k", label: "navigate" },
      { key: "C-\u2190\u2191\u2192\u2193", label: "move focus" },
      { key: "tab", label: "cycle" },
    ];

    if (focusedRegion === "projects") {
      return [
        ...base,
        { key: "b", label: "board" },
        { key: "a", label: "add" },
        { key: "space", label: "actions" },
        { key: "H", label: "assign hotkey" },
        { key: "S", label: "settings" },
      ];
    }
    if (focusedRegion === "supervisors") {
      return [
        ...base,
        { key: "enter", label: "view ticket" },
        { key: "S", label: "settings" },
      ];
    }

    // containers
    return [
      ...base,
      { key: "s", label: "start" },
      { key: "x", label: "stop" },
      { key: "R", label: "remove" },
      { key: "S", label: "settings" },
    ];
  };

  // --- Render main content ---
  const selectedContainer = containers[containerIndex] ?? null;

  const renderMain = () => {
    if (focusedRegion === "containers") {
      if (!selectedContainer) {
        return (
          <Panel title="Containers" focused={false} flexGrow={1}>
            <Box flexDirection="column" paddingX={1}>
              <Text color={th.fgSubtle}>No containers found.</Text>
            </Box>
          </Panel>
        );
      }
      const sc = selectedContainer;
      const stateColor = sc.state === "running" ? th.success : sc.state === "exited" ? th.error : sc.state === "created" ? th.warning : sc.state === "paused" ? th.info : th.fgSubtle;
      return (
        <Panel title={`Container \u203A ${sc.name}`} focused={false} flexGrow={1}>
          <Box flexDirection="column" paddingX={1}>
            <Box><Text color={th.fgSubtle}>Name     </Text><Text color={th.fg}>{sc.name}</Text></Box>
            <Box><Text color={th.fgSubtle}>Image    </Text><Text color={th.fg}>{sc.image}</Text></Box>
            <Box><Text color={th.fgSubtle}>State    </Text><Text color={stateColor} bold>{sc.state}</Text></Box>
            <Box><Text color={th.fgSubtle}>Status   </Text><Text color={th.fg}>{sc.status}</Text></Box>
            <Box><Text color={th.fgSubtle}>Created  </Text><Text color={th.fg}>{sc.created}</Text></Box>
            {sc.ports ? <Box><Text color={th.fgSubtle}>Ports    </Text><Text color={th.fg}>{sc.ports}</Text></Box> : null}
          </Box>
        </Panel>
      );
    }

    if (!selectedProject) {
      return (
        <Panel title="Projects" focused={false} flexGrow={1}>
          <Box flexDirection="column" paddingX={1}>
            <Text color={th.fgSubtle}>No projects configured. Press <Text color={th.accent} bold>a</Text> to add one.</Text>
          </Box>
        </Panel>
      );
    }

    // Project actions menu takes over the content area
    if (projectActions) {
      return (
        <Panel title={`${selectedProject.label} \u203A Actions`} focused flexGrow={1}>
          <Box flexDirection="column" paddingX={1}>
            {projectActions.actions.map((action, i) => (
              <Text key={action.key} color={i === projectActions.selectedIndex ? th.accent : undefined}>
                {i === projectActions.selectedIndex ? "\u25B8 " : "  "}{action.label}
              </Text>
            ))}
            <Box marginTop={1}>
              <Text color={th.fgSubtle}>enter select · esc cancel</Text>
            </Box>
          </Box>
        </Panel>
      );
    }

    // Sandbox picker takes over the content area
    if (sandboxPicker) {
      return (
        <Panel title={`${selectedProject.label} \u203A Sandbox`} focused flexGrow={1}>
          <Box flexDirection="column" paddingX={1}>
            {sandboxPicker.current ? (
              <Text color={th.fgSubtle}>
                Current: {sandboxPicker.current.image
                  ? `image: ${sandboxPicker.current.image}`
                  : sandboxPicker.current.dockerfile
                    ? `dockerfile: ${sandboxPicker.current.dockerfile}`
                    : "none"}
              </Text>
            ) : (
              <Text color={th.fgSubtle}>Current: none (running locally)</Text>
            )}
            {sandboxPicker.inputMode ? (
              <Box marginTop={1}>
                <Text color={th.accent}>{sandboxPicker.inputMode === "image" ? "Image:" : "Dockerfile:"} </Text>
                <Text color={th.fg}>{sandboxPicker.inputValue || <Text color={th.fgSubtle}>{sandboxPicker.inputMode === "image" ? "ubuntu:24.04" : "Dockerfile"}</Text>}</Text>
                <Text color={th.accent}>_</Text>
              </Box>
            ) : (
              <Box flexDirection="column" marginTop={1}>
                {SANDBOX_OPTIONS.map((opt, i) => (
                  <Text key={opt.key} color={i === sandboxPicker.selectedIndex ? th.accent : undefined}>
                    {i === sandboxPicker.selectedIndex ? "\u25B8 " : "  "}{opt.label}
                  </Text>
                ))}
              </Box>
            )}
            <Box marginTop={1}>
              <Text color={th.fgSubtle}>enter select · esc cancel</Text>
            </Box>
          </Box>
        </Panel>
      );
    }

    if (contentView.type === "home") {
      return (
        <ProjectHome
          tilingRoot={tilingState.root}
          focusedTileId={tilingState.focusedId}
          active={focusedRegion.startsWith("tile:")}
          insertMode={mode === "INSERT"}
          sessions={sessionsRef.current}
          pickerOpen={pickerOpen}
          availCols={contentCols}
          availRows={contentRows}
        />
      );
    }

    if (contentView.type === "board") {
      return (
        <Board
          project={selectedProject}
          active={focusedRegion === "content"}
          onSelectTicket={(t) => setContentView({ type: "ticket", ticket: t })}
          onBack={handleReleaseFocus}
          onQuit={handleQuit}
          onInputActive={setInputActive}
          onHints={setSubViewHints}
          onStatus={setSubViewStatus}
        />
      );
    }

    if (contentView.type === "ticket") {
      return (
        <TicketDetail
          ticket={contentView.ticket}
          projectId={selectedProject.id}
          tilingRoot={ticketTilingState.root}
          focusedTileId={ticketTilingState.focusedId}
          active={focusedRegion.startsWith("tile:")}
          insertMode={mode === "INSERT"}
          sessions={ticketSessionsRef.current}
          pickerOpen={ticketPickerOpen}
          availCols={contentCols}
          availRows={contentRows}
          onBack={handleReleaseFocus}
          onQuit={handleQuit}
          onInputActive={setInputActive}
          onHints={setSubViewHints}
          onStatus={setSubViewStatus}
        />
      );
    }

    return null;
  };

  // Reverse hotkey map: project_id -> digit
  const hotkeyByProject = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [digit, projectId] of Object.entries(hotkeys)) {
      map[projectId] = digit;
    }
    return map;
  }, [hotkeys]);

  // --- Loading / Onboarding ---
  if (needsOnboarding === null) {
    return (
      <Box flexDirection="column" height={totalRows}>
        <Text color={th.fgSubtle}>Loading...</Text>
      </Box>
    );
  }

  if (needsOnboarding) {
    return <Onboarding onComplete={() => setNeedsOnboarding(false)} />;
  }

  const w = SIDEBAR_WIDTH - 4;

  return (
    <Box flexDirection="column" height={totalRows}>
      <TitleBar breadcrumbs={breadcrumbs} />

      {overlay === "settings" ? (
        <Settings onQuit={handleCloseOverlay} />
      ) : (
        <>
          <Box flexDirection="row" flexGrow={1}>
            {/* ===== Sidebar ===== */}
            <Box flexDirection="column" width={SIDEBAR_WIDTH} flexShrink={0}>
              {/* Projects panel */}
              <Panel
                title={`Projects (${projects.length})`}
                focused={focusedRegion === "projects"}
                flexGrow={1}
              >
                <Box flexDirection="column">
                  {projects.length === 0 ? (
                    <Text color={th.fgSubtle}> no projects</Text>
                  ) : (
                    projects.map((project, i) => {
                      const isProjectsFocused = focusedRegion === "projects";
                      const isCursor = i === projectListIndex;
                      const hk = hotkeyByProject[project.id];
                      const hkPrefix = hk !== undefined ? `[${hk}]` : "   ";
                      const suffix =
                        (project.sandbox ? " D" : "") +
                        (project.active_count > 0 ? " \u25CF" : "") +
                        (autoDispatchMap[project.id] ? " A" : "");
                      const label = `${hkPrefix} ${truncate(project.label, w - 8)} ${project.ticket_count}${suffix}`;
                      if (isCursor) {
                        return (
                          <Text key={project.id} inverse={isProjectsFocused} color={!isProjectsFocused ? th.accent : undefined}>
                            {padRow(label)}
                          </Text>
                        );
                      }
                      return (
                        <Text key={project.id} color={th.fg}>
                          {hk !== undefined ? <Text color={th.warning}>[{hk}]</Text> : "   "}{" "}{truncate(project.label, w - 8)}
                          <Text color={th.fgSubtle}> {project.ticket_count}</Text>
                          {project.sandbox ? <Text color={th.info}> D</Text> : null}
                          {project.active_count > 0 ? <Text color={th.success}> {"\u25CF"}</Text> : null}
                          {autoDispatchMap[project.id] ? <Text color={th.accentSecondary}> A</Text> : null}
                        </Text>
                      );
                    })
                  )}
                </Box>
              </Panel>

              {/* Supervisors panel */}
              <Panel
                title={`Supervisors (${activeTickets.length})`}
                focused={focusedRegion === "supervisors"}
              >
                <Box flexDirection="column">
                  {activeTickets.length === 0 ? (
                    <Text color={th.fgSubtle}> no active supervisors</Text>
                  ) : (
                    activeTickets.map((t, i) => {
                      const isSupervisorsFocused = focusedRegion === "supervisors";
                      const isCursor = i === supervisorIndex;
                      const elapsed = t.task ? formatTimeAgo(new Date(t.task.status.timestamp).toISOString()) : "";
                      const line1 = ` \u25CF ${truncate(t.title, w - 4)}`;
                      const meta = `${t.project_label}${elapsed ? ` ${elapsed}` : ""}${t.phase ? ` [${t.phase}]` : ""}`;
                      const line2 = `   ${truncate(meta, w - 5)}`;
                      if (isCursor) {
                        return (
                          <Box key={t.id} flexDirection="column">
                            <Text inverse={isSupervisorsFocused} color={!isSupervisorsFocused ? th.accent : undefined}>{padRow(line1)}</Text>
                            <Text inverse={isSupervisorsFocused} color={!isSupervisorsFocused ? th.accent : undefined}>{padRow(line2)}</Text>
                          </Box>
                        );
                      }
                      return (
                        <Box key={t.id} flexDirection="column">
                          <Text color={th.fg}>
                            {" "}
                            <Text color={th.success}>{"\u25CF"}</Text> {truncate(t.title, w - 4)}
                          </Text>
                          <Text color={th.fgSubtle}>{"   "}{meta}</Text>
                        </Box>
                      );
                    })
                  )}
                </Box>
              </Panel>

              {/* Containers panel */}
              <Panel title={`Containers (${containers.length})`} focused={focusedRegion === "containers"}>
                <Box flexDirection="column">
                  {containers.length === 0 ? (
                    <Text color={th.fgSubtle}> none</Text>
                  ) : (
                    containers.map((c, i) => {
                      const isContainersFocused = focusedRegion === "containers";
                      const isCursor = i === containerIndex;
                      const stateColor = c.state === "running" ? th.success : c.state === "exited" ? th.error : th.warning;
                      const dot = c.state === "running" ? "\u25CF" : "\u25CB";
                      const label = ` ${dot} ${truncate(c.name.replace(/^omni-work-/, ""), w - 5)}`;
                      if (isCursor) {
                        return (
                          <Text key={c.name} inverse={isContainersFocused} color={!isContainersFocused ? th.accent : undefined} wrap="truncate">
                            {padRow(label)}
                          </Text>
                        );
                      }
                      return (
                        <Text key={c.name} color={th.fg} wrap="truncate">
                          {" "}
                          <Text color={stateColor}>{dot}</Text>
                          {" "}{truncate(c.name.replace(/^omni-work-/, ""), w - 5)}
                        </Text>
                      );
                    })
                  )}
                </Box>
              </Panel>
            </Box>

            {/* ===== Main content ===== */}
            <Box flexDirection="column" flexGrow={1}>
              {renderMain()}
            </Box>
          </Box>

          {/* Dialogs */}
          {addingProject ? (
            <Box>
              <TextInput
                value={addPath}
                onChange={setAddPath}
                onSubmit={handleAddProject}
                onCancel={() => setAddingProject(false)}
                prompt="Path:"
                placeholder="/path/to/repo"
              />
            </Box>
          ) : null}
          {confirmDelete ? (
            <Box>
              <ConfirmDialog
                message={`Delete "${confirmDelete.label}"?`}
                onConfirm={handleDeleteProject}
                onCancel={() => setConfirmDelete(null)}
              />
            </Box>
          ) : null}
          {/* Status bar */}
          <StatusBar
            hints={getHints()}
            status={focusedRegion === "content" ? subViewStatus : status}
            mode={mode}
          />
        </>
      )}
    </Box>
  );
}
