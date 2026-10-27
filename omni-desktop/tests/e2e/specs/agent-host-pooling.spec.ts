import type { Page } from '@playwright/test';
import { expect, test } from 'tests/e2e/fixtures/test';
import { fileTreeItem, openSession, sessionRow, workspaceFileTree } from 'tests/e2e/support/app';
import { invokeApp } from 'tests/e2e/support/invoke';
import { attachProofScreenshot } from 'tests/e2e/support/proof';

type RunningStatus = {
  type: 'running';
  data: {
    wsUrl?: string;
    agentHostId?: string;
    workspaceId?: string;
    environmentId?: string;
    hasAuthToken: boolean;
  };
};

test.use({ seedState: 'pooled-workspaces' });

type AgentStatus = {
  type?: string;
  data?: {
    wsUrl?: string;
    agentHostId?: string;
    workspaceId?: string;
    environmentId?: string;
    authToken?: string;
  };
};

async function processStatus(page: Page, processId: string): Promise<RunningStatus | { type: string }> {
  const status = await invokeApp<AgentStatus | null>(page, 'agent-process:get-status', processId);
  if (status?.type !== 'running' || !status.data) {
    return { type: status?.type ?? 'uninitialized' };
  }
  return {
    type: 'running',
    data: {
      wsUrl: status.data.wsUrl,
      agentHostId: status.data.agentHostId,
      workspaceId: status.data.workspaceId,
      environmentId: status.data.environmentId,
      hasAuthToken: typeof status.data.authToken === 'string' && status.data.authToken.length > 0,
    },
  };
}

async function openIdentityFile(page: Page, sessionName: string, expectedIdentity: string): Promise<void> {
  await expect(sessionRow(page, sessionName)).toBeVisible({ timeout: 120_000 });
  await openSession(page, sessionName);

  const filesButton = page.getByRole('radio', { name: 'Files', exact: true }).filter({ visible: true });
  await expect(filesButton).toBeVisible({ timeout: 120_000 });
  await filesButton.click();

  const filesSurface = page.getByRole('region', { name: 'Workspace files', exact: true }).filter({ visible: true });
  await expect(filesSurface).toBeVisible({ timeout: 90_000 });
  const tree = await workspaceFileTree(filesSurface);
  const identityFile = fileTreeItem(tree, 'identity.txt');
  await expect(identityFile).toBeVisible({ timeout: 90_000 });
  await identityFile.click();
  await expect(page.getByRole('textbox', { name: 'Editor for identity.txt' })).toHaveText(expectedIdentity);
}

test.describe('AgentHost pooling', () => {
  test('routes two unrelated workspaces through distinct environments on one host', async ({ app }, testInfo) => {
    test.setTimeout(300_000);
    const page = app.page;
    const alphaSession = sessionRow(page, 'Pool Alpha chat');
    await expect(alphaSession).toBeVisible({ timeout: 120_000 });
    await alphaSession.click();

    await expect
      .poll(() => processStatus(page, 'code-e2e-pool-alpha'), { timeout: 180_000 })
      .toMatchObject({ type: 'running' });

    const betaSession = sessionRow(page, 'Pool Beta chat');
    await expect(betaSession).toBeVisible({ timeout: 120_000 });
    await betaSession.click();
    await expect
      .poll(() => processStatus(page, 'code-e2e-pool-beta'), { timeout: 180_000 })
      .toMatchObject({ type: 'running' });

    const alpha = (await processStatus(page, 'code-e2e-pool-alpha')) as RunningStatus;
    const beta = (await processStatus(page, 'code-e2e-pool-beta')) as RunningStatus;
    expect(alpha.data.agentHostId).toBeTruthy();
    expect(alpha.data.agentHostId).toBe(beta.data.agentHostId);
    // Server mode proxies each consumer under its own path, so the browser-facing
    // wsUrl differs per chat even when both reach the same host.
    expect(alpha.data.hasAuthToken).toBe(true);
    expect(beta.data.hasAuthToken).toBe(true);
    expect(alpha.data.workspaceId).not.toBe(beta.data.workspaceId);
    expect(alpha.data.environmentId).not.toBe(beta.data.environmentId);

    await openIdentityFile(page, 'Pool Alpha chat', 'alpha workspace');
    await openIdentityFile(page, 'Pool Beta chat', 'beta workspace');
    await attachProofScreenshot(page, testInfo, 'two workspaces routed through one AgentHost');
  });
});
