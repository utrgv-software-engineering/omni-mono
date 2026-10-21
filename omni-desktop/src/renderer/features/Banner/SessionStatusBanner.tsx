/**
 * Inline status banner for agent-process errors with a fix-it path: an AI
 * provider auth failure links to the AI settings tab. Other errors fall back
 * to the host's own error surface (CodeErrorView, ChatShell
 * `phase: 'error'`); the banner is silent there.
 */
import './Banner.css';

import { TriangleAlert } from 'lucide-react';
import { memo, useCallback } from 'react';

import { classifyAgentError } from '@/lib/provider-config';
import { Alert, AlertDescription, AlertTitle } from '@/renderer/ds/ui/alert';
import { Button } from '@/renderer/ds/ui/button';
import { openSettingsTab } from '@/renderer/features/SettingsModal/settings-nav';
import type { AgentProcessStatus, WithTimestamp } from '@/shared/types';

export type SessionStatusBannerProps = {
  status: WithTimestamp<AgentProcessStatus> | undefined;
};

export const SessionStatusBanner = memo(({ status }: SessionStatusBannerProps) => {
  const handleOpenAiSettings = useCallback(() => openSettingsTab('AI'), []);
  if (status?.type !== 'error') {
    return null;
  }
  const { message } = status.error;
  // Auth failures get a fix-it path: the user's key/subscription is the
  // problem, and the AI tab's connection cards can diagnose and repair it.
  if (message && classifyAgentError(message) === 'auth') {
    return (
      <Alert variant="destructive" className="omni-status-banner-grid absolute inset-x-0 top-0 z-100 m-2" role="status">
        <TriangleAlert />
        <AlertTitle>Your AI provider rejected the request.</AlertTitle>
        <AlertDescription>The key may have expired or been revoked.</AlertDescription>
        <Button className="col-start-3 row-span-2 row-start-1" size="sm" variant="ghost" onClick={handleOpenAiSettings}>
          Check AI settings
        </Button>
      </Alert>
    );
  }
  // Host-level error UI owns the surface, nothing here.
  return null;
});

SessionStatusBanner.displayName = 'SessionStatusBanner';
