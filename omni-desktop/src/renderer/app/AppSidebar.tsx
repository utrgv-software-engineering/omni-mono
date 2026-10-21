import { useStore } from '@nanostores/react';
import { Beaker, CalendarClock, Columns3, Ellipsis, Puzzle, Search, Settings, SquarePen } from 'lucide-react';
import { memo, useCallback, useEffect } from 'react';

import { OmniLogo } from '@/renderer/common/AsciiLogo';
import { cn } from '@/renderer/ds/cn';
import { Button } from '@/renderer/ds/ui/button';
/**
 * The unified app sidebar (desktop): one persistent nav replacing the rail
 * and the per-tab sidebars. Three zones — fixed surface rows (New chat,
 * Spaces); container sections (Projects, Recents); and a pinned management
 * cluster (Routines, Plugins, Settings).
 *
 * On mobile the same nav is an overlay DRAWER (the ChatGPT/Gmail model):
 * the app lands on the working surface, and each surface's header carries a
 * leading affordance that slides this over it. There is no bottom tab bar
 * and no Home screen.
 */ import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/renderer/ds/ui/dropdown-menu';
import {
  Sidebar,
  SIDEBAR_WIDTH,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/renderer/ds/ui/sidebar';
import { RecentsSection } from '@/renderer/features/Code/SessionsSection';
import { codeApi } from '@/renderer/features/Code/state';
import { useRecentConversations } from '@/renderer/features/Code/use-recent-conversations';
import { $commandPaletteOpen } from '@/renderer/features/CommandPalette/CommandPalette';
import { ProjectsSection } from '@/renderer/features/Projects/ProjectsSection';
import { goToRoutine } from '@/renderer/features/ScheduledTasks/state';
import { persistedStoreApi } from '@/renderer/services/store';
import type { LayoutMode } from '@/shared/types';

export const AppSidebar = memo(() => {
  const store = useStore(persistedStoreApi.$atom);
  const { isMobile, setOpenMobile, state: sidebarState } = useSidebar();
  const { recent: recentConversations, sessionTitles } = useRecentConversations(store.codeTabs ?? []);

  // iOS standalone clips element painting at the layout viewport, so the
  // band below it can only ever show the html/body backstop. With the
  // desktop sidebar expanded the app's bottom edge is two surfaces side by
  // side — stamp <html> so tailwind.css can split the backstop and keep the
  // sidebar column sidebar-colored in that band.
  useEffect(() => {
    const root = document.documentElement;
    const clear = () => {
      delete root.dataset.sidebarBackstop;
      root.style.removeProperty('--backstop-sidebar-width');
    };
    if (!isMobile && sidebarState === 'expanded') {
      root.dataset.sidebarBackstop = 'expanded';
      root.style.setProperty('--backstop-sidebar-width', SIDEBAR_WIDTH);
    } else {
      clear();
    }
    return clear;
  }, [isMobile, sidebarState]);

  const closeDrawer = useCallback(() => setOpenMobile(false), [setOpenMobile]);
  const setMode = useCallback(
    (mode: LayoutMode) => {
      persistedStoreApi.setKey('layoutMode', mode);
      closeDrawer();
    },
    [closeDrawer]
  );
  const handleNewChat = useCallback(() => {
    void codeApi.openFreshChat();
    setMode('chat');
  }, [setMode]);
  const handleSpaces = useCallback(() => {
    codeApi.setLayoutMode('tile');
    setMode('chat');
  }, [setMode]);
  const handleRoutines = useCallback(() => {
    goToRoutine();
    closeDrawer();
  }, [closeDrawer]);
  const handlePlugins = useCallback(() => setMode('plugins'), [setMode]);
  const handleGallery = useCallback(() => setMode('gallery'), [setMode]);
  const handleSettings = useCallback(() => setMode('settings'), [setMode]);
  const handlePalette = useCallback(() => $commandPaletteOpen.set(true), []);

  // Selection derives from (layoutMode, per-feature view atoms) — the same
  // derivation the per-tab sidebars used, widened to the app.
  const mode = store.layoutMode;
  const routinesSelected = mode === 'routines';
  // The More row owns every management surface — it paints selected when
  // any of them is frontmost, so "where am I" survives the collapse.
  const moreSelected = routinesSelected || mode === 'plugins' || mode === 'gallery';

  return (
    <Sidebar collapsible="offcanvas">
      <SidebarHeader className="p-0">
        <div className="flex shrink-0 items-center gap-2 pt-8 pr-1 pb-5 pl-5">
          <OmniLogo />
          <h2 className={cn('min-w-0 truncate font-display text-lg font-semibold tracking-tight')}>Omni</h2>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="ml-auto"
            aria-label="Open command palette"
            onClick={handlePalette}
          >
            <Search />
          </Button>
        </div>
      </SidebarHeader>
      <SidebarContent>
        {/* ── Fixed surface rows ── */}
        <SidebarGroup className="py-0">
          <SidebarGroupContent>
            <SidebarMenu aria-label="Surfaces">
              {/* Action row, never selected. */}
              <SidebarMenuItem>
                <SidebarMenuButton onClick={handleNewChat} tooltip="New chat">
                  <SquarePen />
                  <span>New chat</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={mode === 'chat' && store.codeLayoutMode === 'tile'}
                  onClick={handleSpaces}
                  tooltip="Spaces"
                >
                  <Columns3 />
                  <span>Spaces</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* ── Container sections ── */}
        <ProjectsSection sessionTitles={sessionTitles} onNavigate={closeDrawer} />
        <RecentsSection recent={recentConversations} sessionTitles={sessionTitles} onNavigate={closeDrawer} />
      </SidebarContent>

      {/* ── Management footer — the occasional surfaces live behind "More"
              (the Slack-rail idiom); Settings keeps direct access by universal
              convention. ── */}
      <SidebarFooter className="border-t border-sidebar-border">
        <SidebarMenu aria-label="Management">
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <SidebarMenuButton isActive={moreSelected} tooltip="More">
                  <Ellipsis />
                  <span>More</span>
                </SidebarMenuButton>
              </DropdownMenuTrigger>
              <>
                <DropdownMenuContent>
                  <DropdownMenuItem onClick={handleRoutines}>
                    <CalendarClock />
                    Routines
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={handlePlugins}>
                    <Puzzle />
                    Plugins
                  </DropdownMenuItem>
                  {import.meta.env.DEV && (
                    <DropdownMenuItem onClick={handleGallery}>
                      <Beaker />
                      Gallery
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </>
            </DropdownMenu>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton isActive={mode === 'settings'} onClick={handleSettings} tooltip="Settings">
              <Settings />
              <span>Settings</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
});
AppSidebar.displayName = 'AppSidebar';
