import type { Page } from '@playwright/test';
import { expect, test } from 'tests/e2e/fixtures/test';
import { sessionRow } from 'tests/e2e/support/app';
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
  const session = sessionRow(page, sessionName);
  await expect(session).toBeVisible({ timeout: 120_000 });
  await session.click();

  const filesButton = page.getByRole('radio', { name: 'Files', exact: true });
  await expect(filesButton).toBeVisible({ timeout: 120_000 });
  await filesButton.click();

  const filesSurface = page.getByRole('region', { name: 'Workspace files', exact: true });
  await expect(filesSurface).toBeVisible({ timeout: 90_000 });
  const tree = filesSurface.getByRole('tree', { name: 'Workspace files' });
  const identityFile = tree.getByRole('treeitem', { name: 'identity.txt', exact: true });
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
    expect(alpha.data.wsUrl).toBe(beta.data.wsUrl);
    expect(alpha.data.hasAuthToken).toBe(true);
    expect(beta.data.hasAuthToken).toBe(true);
    expect(alpha.data.workspaceId).not.toBe(beta.data.workspaceId);
    expect(alpha.data.environmentId).not.toBe(beta.data.environmentId);

    await openIdentityFile(page, 'Pool Alpha chat', 'alpha workspace');
    await openIdentityFile(page, 'Pool Beta chat', 'beta workspace');
    await attachProofScreenshot(page, testInfo, 'two workspaces routed through one AgentHost');
  });
});
