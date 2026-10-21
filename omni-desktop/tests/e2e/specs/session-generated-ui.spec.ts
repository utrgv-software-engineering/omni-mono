import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, newDeckChat, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

test('keeps generated plans and artifacts in their session through updates and reconnect', async ({
  app,
}, testInfo) => {
  test.setTimeout(360_000);
  const page = app.page;
  // Track real sockets so reconnect exercises the normal event replay path.
  await page.evaluate(() => {
    const send = WebSocket.prototype.send;
    const sockets = new Set<WebSocket>();
    const interrupted = new Set<string>();
    (window as any).interruptedTiles = () => interrupted.size;
    WebSocket.prototype.send = function (data) {
      if (!sockets.has(this)) {
        const original = this.onmessage;
        this.onmessage = function (event) {
          original?.call(this, event);
          let frame;
          try {
            frame = JSON.parse(String(event.data));
          } catch {
            return;
          }
          const p = frame.params;
          if (
            frame.method === 'client_request' &&
            p?.function === 'ui.add_artifact' &&
            String(p.args?.content).endsWith('_updated') &&
            !interrupted.has(p.session_id)
          ) {
            interrupted.add(p.session_id);
            this.close(4001, 'interrupt active artifact update');
          }
        };
      }
      sockets.add(this);
      return send.call(this, data);
    };
    (window as any).openTileSockets = () =>
      [...sockets].filter((socket) => socket.readyState === WebSocket.OPEN).length;
    (window as any).disconnectTileSockets = () => {
      const count = (window as any).openTileSockets();
      [...sockets].forEach((socket) => socket.close(4001, 'tile test'));
      return count;
    };
  });
  const composer = visibleComposer(page);
  await expect(composer).toBeVisible({ timeout: 90_000 });
  const aId = await activeSessionId(page);
  await composer.fill('TILE_UI_A');
  await composer.press('Enter');
  await newDeckChat(page);
  await expect.poll(() => activeSessionId(page)).not.toBe(aId);
  const bId = await activeSessionId(page);
  const a = sessionPane(page, aId);
  const b = sessionPane(page, bId);
  await composer.fill('TILE_UI_B');
  await composer.press('Enter');
  const sessions = [
    [a, 'A', 'TILE_UI_A'],
    [b, 'B', 'TILE_UI_B'],
  ] as const;
  for (const [column, id, title] of sessions) {
    await openSession(page, title);
    await expect(column.getByText(`${id}_ARTIFACT_initial`, { exact: true })).toBeVisible({ timeout: 240_000 });
    await expect(column.getByText(`${id}_PLAN_initial`, { exact: true }).first()).toBeVisible({ timeout: 90_000 });
    await expect(column).not.toContainText(`${id === 'A' ? 'B' : 'A'}_ARTIFACT_initial`);
    await expect(column).not.toContainText(`${id === 'A' ? 'B' : 'A'}_PLAN_initial`);
    await attachProofPng(testInfo, `${id} initial plan and artifact`, await app.captureScreenshot());
  }
  for (const [, , title] of sessions) {
    await openSession(page, title);
    await composer.fill('UPDATE_TILE_UI');
    await composer.press('Enter');
  }
  for (const [column, id, title] of sessions) {
    await openSession(page, title);
    await expect(column.getByText(`${id}_ARTIFACT_updated`, { exact: true })).toBeVisible({ timeout: 90_000 });
    await expect(column.getByText(`${id}_PLAN_updated`, { exact: true }).first()).toBeVisible({ timeout: 90_000 });
    await expect(column.getByText(`${id}_ARTIFACT_initial`, { exact: true })).toHaveCount(0);
    await column.getByRole('button', { name: 'Maximize', exact: true }).click();
    await expect(column.getByText(`${id}_ARTIFACT_updated`, { exact: true })).toBeVisible();
    await attachProofPng(testInfo, `${id} artifact expanded within its session`, await app.captureScreenshot());
    await column.getByRole('button', { name: 'Restore', exact: true }).click();
  }
  const connectionCount = await page.evaluate(() => (window as any).disconnectTileSockets());
  expect(connectionCount).toBeGreaterThan(0);
  await expect
    .poll(() => page.evaluate(() => (window as any).openTileSockets()), { timeout: 90_000 })
    .toBe(connectionCount);
  for (const [column, id, title] of sessions) {
    await openSession(page, title);
    await expect(composer).toBeEditable({ timeout: 90_000 });
    await expect(column.getByText(`${id}_ARTIFACT_updated`, { exact: true })).toHaveCount(1);
    await expect(column.getByText(`${id}_PLAN_updated`, { exact: true }).first()).toBeVisible();
    await expect(column).not.toContainText(`${id === 'A' ? 'B' : 'A'}_ARTIFACT_updated`);
    await expect(column).not.toContainText(`${id === 'A' ? 'B' : 'A'}_PLAN_updated`);
  }
  await attachProofPng(testInfo, 'updated plans and artifacts after reconnect', await app.captureScreenshot());
  await expect.poll(() => page.evaluate(() => (window as any).interruptedTiles())).toBe(2);

  // A same-ID artifact in the other session must never become the scroll target.
  await page.evaluate(() => {
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (options) {
      if (this.hasAttribute('data-artifact-id')) {
        (window as any).artifactScrollOwner = this.closest('[data-deck-column]')?.getAttribute('data-deck-column');
      }
      original.call(this, options);
    };
  });
  await openSession(page, 'TILE_UI_B');
  await b.getByRole('button', { name: 'Toggle artifacts', exact: true }).click();
  await b.getByRole('button', { name: 'B artifact', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).artifactScrollOwner)).toBe(bId);
  await openSession(page, 'TILE_UI_A');
  await composer.fill('Focused draft A');
  await openSession(page, 'TILE_UI_B');
  await openSession(page, 'TILE_UI_A');
  await expect(composer).toHaveValue('Focused draft A');
  await page.reload();
  for (const [column, id, title] of sessions) {
    await openSession(page, title);
    await expect(column.getByText(`${id}_ARTIFACT_updated`, { exact: true })).toBeVisible({ timeout: 90_000 });
    await expect(column.getByText(`${id}_ARTIFACT_updated`, { exact: true })).toHaveCount(1);
    await expect(column.getByText(`${id}_PLAN_updated`, { exact: true }).first()).toBeVisible();
    await expect(column).not.toContainText(`${id === 'A' ? 'B' : 'A'}_ARTIFACT_updated`);
    await expect(column).not.toContainText(`${id === 'A' ? 'B' : 'A'}_PLAN_updated`);
  }
  await attachProofPng(testInfo, 'plans and artifacts recovered after full reload', await app.captureScreenshot());
});
