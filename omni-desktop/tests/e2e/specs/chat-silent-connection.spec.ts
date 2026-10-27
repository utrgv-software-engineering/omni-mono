import type { WebSocketRoute } from '@playwright/test';
import { expect, test } from 'tests/e2e/fixtures/test';
import {
  activeSessionId,
  expectRunFinished,
  newDeckChat,
  openSession,
  sessionPane,
  visibleComposer,
} from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

test('recovers silently stalled RPC sockets while preserving each visible tile draft', async ({ app }, info) => {
  test.setTimeout(240_000);
  const page = app.page;
  const rpcSockets = new Set<WebSocketRoute>();
  const stalled = new Set<WebSocketRoute>();
  const recoveredEndpoints = new Set<string>();
  const endpoint = (socket: WebSocketRoute) => {
    const url = new URL(socket.url());
    url.search = '';
    return url.toString();
  };
  await page.routeWebSocket('**/*', (socket) => {
    const server = socket.connectToServer();
    socket.onMessage((data) => {
      let frame;
      try {
        frame = JSON.parse(String(data));
      } catch {}
      if (frame?.method === 'initialize') {
        rpcSockets.add(socket);
      }
      if (frame?.method === 'initialized' && stalled.size && !stalled.has(socket)) {
        recoveredEndpoints.add(endpoint(socket));
      }
      if (!stalled.has(socket)) {
        server.send(data);
      }
    });
    server.onMessage((data) => {
      if (!stalled.has(socket)) {
        socket.send(data);
      }
    });
    socket.onClose((code, reason) => {
      rpcSockets.delete(socket);
      void server.close({ code, reason });
    });
    server.onClose((code, reason) => {
      rpcSockets.delete(socket);
      void socket.close({ code, reason });
    });
  });
  await page.reload();
  const composer = visibleComposer(page);
  await expect(composer).toBeVisible({ timeout: 90_000 });
  const firstId = await activeSessionId(page);
  await composer.fill('SILENT_CONNECTION_SETUP');
  await composer.press('Enter');
  await expect(page.getByText('HOST_FIRST_MESSAGE_READY', { exact: true })).toBeVisible({ timeout: 90_000 });
  await newDeckChat(page);
  await expect.poll(() => activeSessionId(page)).not.toBe(firstId);
  const first = sessionPane(page, firstId);
  const second = sessionPane(page, await activeSessionId(page));
  await composer.fill('SILENT_SECOND_SESSION');
  await composer.press('Enter');
  await expect(second.getByText('HOST_FIRST_MESSAGE_READY', { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(composer).toBeEditable();
  await composer.fill('independent second session draft');
  await openSession(page, 'SILENT_CONNECTION_SETUP');
  await expect(composer).toBeEditable();
  await composer.fill('draft kept through silent connection failure');
  expect(rpcSockets.size).toBeGreaterThan(0);
  for (const socket of rpcSockets) {
    stalled.add(socket);
  }
  const stalledEndpoints = new Set([...stalled].map(endpoint));
  // Neither endpoint is closed by the fixture. Application frames disappear
  // in both directions; only the client's liveness check can trigger recovery.
  await expect
    .poll(() => [...stalledEndpoints].every((url) => recoveredEndpoints.has(url)), { timeout: 90_000 })
    .toBe(true);
  // Management and chat can use separate connections to the same endpoint.
  // A replacement URL alone is not proof that every original socket retired.
  await expect.poll(() => [...stalled].every((socket) => !rpcSockets.has(socket)), { timeout: 90_000 }).toBe(true);
  await expect(composer).toBeEditable({ timeout: 90_000 });
  await expect(composer).toHaveValue('draft kept through silent connection failure');
  await openSession(page, 'SILENT_SECOND_SESSION');
  await expect(composer).toBeEditable({ timeout: 90_000 });
  await expect(composer).toHaveValue('independent second session draft');
  await openSession(page, 'SILENT_CONNECTION_SETUP');
  // Reconnects for different endpoints finish independently. Unlike press(),
  // clicking Send waits for the button to be actionable through hydration.
  await first.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    first.getByRole('log').getByText('draft kept through silent connection failure', { exact: true })
  ).toHaveCount(1, { timeout: 90_000 });
  await expectRunFinished(page);
  await openSession(page, 'SILENT_SECOND_SESSION');
  await expect(second.getByRole('log')).not.toContainText('draft kept through silent connection failure');
  await expect(composer).toHaveValue('independent second session draft');
  await attachProofPng(info, 'silent socket recovered with draft intact', await app.captureScreenshot());
});
