import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, newDeckChat, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

for (const interaction of ['question', 'approval'] as const) {
  test(`recovers two pending ${interaction}s through reconnect and reload`, async ({ app }, testInfo) => {
    test.setTimeout(360_000);
    const page = app.page;
    await page.evaluate(() => {
      const sockets = new Set<WebSocket>();
      const send = WebSocket.prototype.send;
      WebSocket.prototype.send = function (data) {
        sockets.add(this);
        return send.call(this, data);
      };
      (window as any).openPendingSockets = () =>
        [...sockets].filter((socket) => socket.readyState === WebSocket.OPEN).length;
      (window as any).disconnectPendingSockets = () => {
        const open = [...sockets].filter((socket) => socket.readyState === WebSocket.OPEN);
        open.forEach((socket) => socket.close(4001, 'pending interaction recovery audit'));
        return open.length;
      };
    });
    const composer = visibleComposer(page);
    const promptA = `TILE_${interaction.toUpperCase()}_A`;
    const promptB = `TILE_${interaction.toUpperCase()}_B`;
    await expect(composer).toBeVisible({ timeout: 90_000 });
    const aId = await activeSessionId(page);
    await composer.fill(promptA);
    await composer.press('Enter');
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeVisible({ timeout: 240_000 });
    await newDeckChat(page);
    await expect.poll(() => activeSessionId(page)).not.toBe(aId);
    const bId = await activeSessionId(page);
    const a = sessionPane(page, aId);
    const b = sessionPane(page, bId);
    await composer.fill(promptB);
    await composer.press('Enter');
    const pending = (column: typeof a, id: string) =>
      interaction === 'question'
        ? column.getByText(`Question for tile ${id}`, { exact: true })
        : column.getByRole('button', { name: 'Approve Once', exact: true });
    const checkBoth = async () => {
      for (const [column, id, title, other] of [
        [a, 'A', promptA, promptB],
        [b, 'B', promptB, promptA],
      ] as const) {
        await openSession(page, title);
        await expect(pending(column, id)).toBeVisible({ timeout: 90_000 });
        await expect(pending(column, id)).toHaveCount(1);
        await expect(composer).toBeEditable();
        await expect(column.getByRole('log')).not.toContainText(other);
      }
    };
    await checkBoth();
    const disconnected = await page.evaluate(() => (window as any).disconnectPendingSockets());
    expect(disconnected).toBeGreaterThan(0);
    await expect
      .poll(() => page.evaluate(() => (window as any).openPendingSockets()), { timeout: 90_000 })
      .toBe(disconnected);
    // A reload below is a second, separate boundary: reconnect retains the
    // controller's maps, whereas reload must reconstruct everything from the server.
    await expect(composer).toBeEditable({ timeout: 90_000 });
    await checkBoth();
    await page.reload();
    await checkBoth();
    await openSession(page, promptA);
    await attachProofPng(testInfo, `two pending ${interaction}s after reload`, await app.captureScreenshot());
    if (interaction === 'question') {
      const input = composer;
      // Let the server accept A's answer, then lose only its acknowledgement.
      // The retry after reconnect must consult A's receipt, not answer B.
      await page.evaluate(() => {
        const send = WebSocket.prototype.send;
        WebSocket.prototype.send = function (data) {
          let frame;
          try {
            frame = JSON.parse(String(data));
          } catch {}
          if (frame?.method === 'client_response' && frame.params?.result?.reply === 'Answer only A after reload') {
            WebSocket.prototype.send = send;
            const original = this.onmessage;
            this.onmessage = function (event) {
              let response;
              try {
                response = JSON.parse(String(event.data));
              } catch {}
              if (response?.id === frame.id) {
                (window as any).lostAnswerAcknowledgement = true;
                this.close(4001, 'lose accepted answer acknowledgement');
                return;
              }
              original?.call(this, event);
            };
          }
          return send.call(this, data);
        };
      });
      await input.fill('Answer only A after reload');
      await input.press('Enter');
      await expect.poll(() => page.evaluate(() => (window as any).lostAnswerAcknowledgement)).toBe(true);
      await expect(input).toBeEditable({ timeout: 90_000 });
      await expect(input).toHaveValue('Answer only A after reload');
      await input.press('Enter');
      await expect(input).toHaveValue('', { timeout: 90_000 });
    } else {
      await pending(a, 'A').click();
    }
    await expect(a.getByText('HOST_FIRST_MESSAGE_READY', { exact: true }).last()).toBeVisible({ timeout: 90_000 });
    if (interaction === 'approval') {
      await expect(pending(a, 'A')).toHaveCount(0);
    }
    await openSession(page, promptB);
    await expect(pending(b, 'B')).toBeVisible();
    await page.reload();
    await openSession(page, promptB);
    await expect(pending(b, 'B')).toBeVisible({ timeout: 90_000 });
    if (interaction === 'approval') {
      await b.getByRole('button', { name: 'Reject', exact: true }).click();
      await expect(pending(b, 'B')).toHaveCount(0);
    } else {
      await composer.fill('Answer only B after reload');
      await composer.press('Enter');
    }
    await expect(b.getByText('HOST_FIRST_MESSAGE_READY', { exact: true }).last()).toBeVisible({ timeout: 90_000 });
    await expect(b.getByRole('log')).not.toContainText('Answer only A after reload');
    await openSession(page, promptA);
    await expect(a.getByRole('log')).not.toContainText('Answer only B after reload');
    await attachProofPng(testInfo, `independently resolved ${interaction}s`, await app.captureScreenshot());
  });
}
