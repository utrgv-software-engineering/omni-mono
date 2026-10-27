import path from 'node:path';

import type { Locator, Page } from '@playwright/test';
import { expect, test } from 'tests/e2e/fixtures/test';
import { openPlugins } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';
import { E2E_MCP_CREATED_SERVER_NAME, E2E_MCP_FIXTURE_FILE } from 'tests/e2e/support/state';

test.use({ seedState: 'mcp-fixture' });

function connectorCard(page: Page, serverName: string): Locator {
  return page.locator('[data-slot="item"]').filter({ hasText: serverName });
}

async function openConnectors(page: Page): Promise<void> {
  await openPlugins(page);
  await page.getByRole('radio', { name: 'Connectors', exact: true }).click();
}

test.describe('MCP connectors', () => {
  test('keeps canonical create and update durable and never resurrects a deleted connector', async ({
    app,
    mode,
  }, testInfo) => {
    test.skip(mode !== 'electron-local', 'Host-scoped MCP configuration belongs to the Electron-local runtime');
    test.setTimeout(420_000);

    await openConnectors(app.page);
    const fixturePath = path.join(app.workspaceDir, E2E_MCP_FIXTURE_FILE);

    await app.page.getByRole('button', { name: 'Add MCP server', exact: true }).click();
    const createDialog = app.page.getByRole('dialog', { name: 'Add MCP server' });
    await createDialog.locator('input[placeholder="my-server"]').fill(E2E_MCP_CREATED_SERVER_NAME);
    await createDialog.locator('input[placeholder="npx"]').fill(process.execPath);
    await createDialog.locator('input[placeholder="arg1, arg2"]').fill(fixturePath);
    await createDialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(createDialog).toBeHidden({ timeout: 120_000 });
    const created = connectorCard(app.page, E2E_MCP_CREATED_SERVER_NAME);
    await expect(created).toBeVisible();
    await expect.poll(() => app.inspectMcpConfig().createdServerPresent).toBe(true);

    await created.getByRole('button', { name: 'Configure' }).click();
    const editDialog = app.page.getByRole('dialog', { name: `Configure "${E2E_MCP_CREATED_SERVER_NAME}"` });
    await editDialog.locator('input[placeholder="arg1, arg2"]').fill(`${fixturePath}, --created-v2`);
    await editDialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(editDialog).toBeHidden();
    await expect.poll(() => app.inspectMcpConfig().createdServerUpdated).toBe(true);
    await attachProofPng(testInfo, 'canonical MCP create and update', await app.captureScreenshot());

    const restarted = await app.restart();
    await openConnectors(restarted);
    const durable = connectorCard(restarted, E2E_MCP_CREATED_SERVER_NAME);
    await expect(durable).toContainText('--created-v2', { timeout: 120_000 });
    expect(app.inspectMcpConfig()).toMatchObject({ createdServerPresent: true, createdServerUpdated: true });

    await durable.getByRole('button', { name: `Remove ${E2E_MCP_CREATED_SERVER_NAME}` }).click();
    const confirmation = restarted.getByRole('alertdialog', {
      name: `Remove "${E2E_MCP_CREATED_SERVER_NAME}"?`,
    });
    await confirmation.getByRole('button', { name: 'Remove', exact: true }).click();
    await expect(durable).toHaveCount(0);
    await expect.poll(() => app.inspectMcpConfig().createdServerPresent).toBe(false);

    const afterDeleteRestart = await app.restart();
    await openConnectors(afterDeleteRestart);
    await expect(connectorCard(afterDeleteRestart, E2E_MCP_CREATED_SERVER_NAME)).toHaveCount(0);
    expect(app.inspectMcpConfig()).toMatchObject({ createdServerPresent: false });
    await attachProofPng(testInfo, 'deleted MCP connector does not resurrect', await app.captureScreenshot());
  });
});
