import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, newDeckChat, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

test('archives one active session without stopping its neighbor, including a lost archive reply', async ({
  app,
  mode,
}, testInfo) => {
  test.setTimeout(360_000);
  const page = app.page;
  const composer = visibleComposer(page);
  await expect(composer).toBeVisible({ timeout: 90_000 });
  const ownerId = await activeSessionId(page);
  await composer.fill('TILE_APPROVAL_A');
  await composer.press('Enter');
  await expect(page.getByRole('button', { name: 'Approve Once', exact: true })).toBeVisible({ timeout: 240_000 });
  const original = app.storedTabs().find((tab) => tab.id === ownerId)!;
  const a = sessionPane(page, ownerId);
  await newDeckChat(page);
  await expect.poll(() => activeSessionId(page)).not.toBe(ownerId);
  const b = sessionPane(page, await activeSessionId(page));
  await composer.fill('TILE_APPROVAL_B');
  await composer.press('Enter');
  await expect(b.getByRole('button', { name: 'Approve Once', exact: true })).toBeVisible({ timeout: 90_000 });
  if (mode === 'server-local') {
    await page.evaluate(() => {
      const send = WebSocket.prototype.send;
      WebSocket.prototype.send = function (data) {
        let frame;
        try {
          frame = JSON.parse(String(data));
        } catch {}
        if (frame?.type === 'invoke') {
          (window as any).managementSocket = this;
        }
        if (frame?.channel === 'store:chat-command' && frame.args?.[0]?.method === 'archiveTab') {
          const message = this.onmessage;
          this.onmessage = function (event) {
            let reply;
            try {
              reply = JSON.parse(String(event.data));
            } catch {}
            if (reply?.id === frame.id) {
              (window as any).archiveReplyDropped = true;
              this.close(4001, 'lost archive reply');
              return;
            }
            message?.call(this, event);
          };
        }
        send.call(this, data);
      };
    });
  }
  await openSession(page, 'TILE_APPROVAL_A');
  await a.getByRole('button', { name: 'Session menu', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Archive session', exact: true }).click();
  await expect(a).toHaveCount(0, { timeout: 90_000 });
  await openSession(page, 'TILE_APPROVAL_B');
  await expect(b.getByRole('button', { name: 'Approve Once', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Archived sessions', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Restore', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('No archived sessions');
  await page.keyboard.press('Escape');
  await openSession(page, 'TILE_APPROVAL_A');
  await expect.poll(() => app.storedTabs().find((tab) => tab.sessionId === original.sessionId)?.id).not.toBeUndefined();
  const reopened = app.storedTabs().find((tab) => tab.sessionId === original.sessionId)!;
  expect(reopened.id).not.toBe(original.id);
  const restored = sessionPane(page, reopened.id);
  await expect(restored.getByRole('log')).toContainText('TILE_APPROVAL_A');
  await expect(restored.getByRole('button', { name: 'Approve Once', exact: true })).toHaveCount(0);
  await expect(restored.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);
  if (mode === 'server-local') {
    await expect.poll(() => page.evaluate(() => (window as any).archiveReplyDropped), { timeout: 90_000 }).toBe(true);
    await expect
      .poll(
        async () =>
          page.evaluate(async (id) => {
            const socket: WebSocket | undefined = (window as any).managementSocket;
            if (!socket || socket.readyState !== WebSocket.OPEN) {
              return 'connecting';
            }
            return new Promise<string>((resolve) => {
              const requestId = -Date.now();
              const done = (value: string) => {
                clearTimeout(timer);
                socket.removeEventListener('message', receive);
                resolve(value);
              };
              const receive = (event: MessageEvent) => {
                const response = JSON.parse(String(event.data));
                if (response.id === requestId) {
                  done(response.result?.type ?? 'unknown');
                }
              };
              const timer = setTimeout(() => done('timeout'), 2000);
              socket.addEventListener('message', receive);
              socket.send(
                JSON.stringify({ type: 'invoke', id: requestId, channel: 'agent-process:get-status', args: [id] })
              );
            });
          }, ownerId),
        { timeout: 90_000 }
      )
      .toBe('uninitialized');
  }
  await attachProofPng(testInfo, 'restored session has no stale approval', await app.captureScreenshot());
  await openSession(page, 'TILE_APPROVAL_B');
  await b.getByRole('button', { name: 'Approve Once', exact: true }).click();
  await expect(b.getByRole('log')).toContainText('HOST_FIRST_MESSAGE_READY', { timeout: 90_000 });
  await openSession(page, 'TILE_APPROVAL_A');
  await composer.fill('Continue the restored conversation');
  await composer.press('Enter');
  await expect(restored.getByRole('log')).toContainText('HOST_FIRST_MESSAGE_READY', { timeout: 90_000 });
  await expect(restored.getByRole('button', { name: 'Approve Once', exact: true })).toHaveCount(0);
  await attachProofPng(testInfo, 'restored conversation runs again', await app.captureScreenshot());
});
