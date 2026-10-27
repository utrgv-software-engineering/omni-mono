import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';

import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, newDeckChat, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';

test.use({ seedState: 'lazy-host-first-message' });

test('a session keeps its browser page across session switches and sidecar toggles', async ({ app }) => {
  test.setTimeout(300_000);
  // Every page load hits this server, so a reload is counted, not inferred.
  let loads = 0;
  const server = createServer((req, res) => {
    if (req.url === '/') {
      loads += 1;
    }
    res.writeHead(200, { 'content-type': 'text/html' });
    res.end('<!doctype html><title>Sidecar page</title><h1>Sidecar page</h1>');
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}/`;
    const page = app.page;
    const composer = visibleComposer(page);
    await expect(composer).toBeVisible({ timeout: 90_000 });
    const aId = await activeSessionId(page);
    const a = sessionPane(page, aId);
    await composer.fill('SIDECAR_A');
    await composer.press('Enter');
    await expect(a.getByText('HOST_FIRST_MESSAGE_READY', { exact: true })).toBeVisible({ timeout: 120_000 });
    await a.getByRole('button', { name: 'Show apps', exact: true }).click();
    await page.getByRole('button', { name: 'Browser', exact: true }).click();
    const address = page.getByPlaceholder('Search or enter URL').filter({ visible: true });
    await address.fill(origin);
    await address.press('Enter');
    await expect.poll(() => loads, { timeout: 30_000 }).toBe(1);

    await newDeckChat(page);
    await expect.poll(() => activeSessionId(page)).not.toBe(aId);
    await composer.fill('SIDECAR_B');
    await composer.press('Enter');
    for (const title of ['SIDECAR_A', 'SIDECAR_B', 'SIDECAR_A']) {
      await openSession(page, title);
    }
    await expect(address).toHaveValue(origin);
    await a.getByRole('button', { name: 'Hide apps', exact: true }).click();
    await expect(address).toHaveCount(0);
    await a.getByRole('button', { name: 'Show apps', exact: true }).click();
    await expect(address).toHaveValue(origin);
    // Give a reload time to land before asserting there was none.
    await page.waitForTimeout(2_000);
    expect(loads).toBe(1);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
