import '@/renderer/styles/tailwind.css';
import '@fontsource-variable/inter';
import '@fontsource-variable/space-grotesk';
import '@/renderer/features/Toast/ipc-toast-listener';
import '@/renderer/features/Toast/status-toast-listener';

import { useStore } from '@nanostores/react';
import { applyPwaTheme, applyTheme } from '@omniagents/ui/themes';
import { TooltipProvider } from '@omniagents/ui/tooltip';
import { MotionConfig } from 'framer-motion';
import { useEffect, useLayoutEffect } from 'react';
import { ErrorBoundary } from 'react-error-boundary';

import { DesktopChatHost } from '@/renderer/app/DesktopChatHost';
import { ErrorBoundaryFallback } from '@/renderer/app/ErrorBoundaryFallback';
import { MainContent } from '@/renderer/app/MainContent';
import { StatusAnnouncer } from '@/renderer/app/StatusAnnouncer';
import { ViewportDebug } from '@/renderer/common/ViewportDebug';
import { SystemInfoLoadingGate, SystemInfoProvider } from '@/renderer/contexts/SystemInfoContext';
import { ConnectionStatusBanner } from '@/renderer/features/Banner/ConnectionStatusBanner';
import { CommandPalette } from '@/renderer/features/CommandPalette/CommandPalette';
import { ToastContainer } from '@/renderer/features/Toast/ToastContainer';
import { VoiceHotkeys } from '@/renderer/features/Voice/VoiceHotkeys';
import { ProductManagementProvider } from '@/renderer/omniagents-ui/product-management-context';
import { initAgentAttention } from '@/renderer/services/agent-attention';
import { initAppHistory } from '@/renderer/services/app-history';
import { initPwaInstall } from '@/renderer/services/pwa-install';
import { persistedStoreApi } from '@/renderer/services/store';

import { useAppHeight } from './use-app-height';
import { usePreloadTerminalFont } from './use-preload-terminal-font';

export const App = () => {
  usePreloadTerminalFont();
  useAppHeight();
  const store = useStore(persistedStoreApi.$atom);

  // Platform shell (Phase 8): history/back + document.title, app badge +
  // notifications, PWA install capture. All idempotent.
  useEffect(() => {
    initAppHistory();
    initAgentAttention();
    initPwaInstall();
  }, []);

  const themeName = store.theme ?? 'omni';
  const textScale = store.textScale ?? 100;
  // Root font-size scales every rem-based surface.
  useEffect(() => {
    document.documentElement.style.fontSize = textScale === 100 ? '' : `${textScale}%`;
  }, [textScale]);

  useLayoutEffect(() => {
    // shadcn themes are direct semantic CSS variables selected by data-theme.
    applyTheme(themeName);
    applyPwaTheme(themeName);
  }, [themeName]);

  return (
    <TooltipProvider delayDuration={300}>
      {/* All framer-motion animations respect the OS reduce-motion setting.
          Hand-written CSS animations carry their own media-query overrides. */}
      <MotionConfig reducedMotion="user">
        <SystemInfoProvider>
          <DesktopChatHost>
            <div className="app-shell relative flex w-dvw flex-col overflow-hidden bg-background font-sans text-foreground antialiased">
              <ErrorBoundary FallbackComponent={ErrorBoundaryFallback}>
                <SystemInfoLoadingGate>
                  <ProductManagementProvider>
                    <ConnectionStatusBanner />
                    <div className="flex min-h-0 w-full flex-1">
                      <MainContent />
                    </div>
                    <VoiceHotkeys />
                    <CommandPalette />
                    <ViewportDebug />
                    <StatusAnnouncer />
                  </ProductManagementProvider>
                </SystemInfoLoadingGate>
                <ToastContainer />
              </ErrorBoundary>
            </div>
          </DesktopChatHost>
        </SystemInfoProvider>
      </MotionConfig>
    </TooltipProvider>
  );
};
