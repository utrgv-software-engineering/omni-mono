import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, newDeckChat, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

test('isolates three mixed sessions through seeded switching churn, duplicate events, reconnect and uploaded drafts', async ({
  app,
}, testInfo) => {
  test.setTimeout(360_000);
  const page = app.page;
  await page.evaluate(() => {
    const sockets = new Set<WebSocket>();
    const originalSend = WebSocket.prototype.send;
    (window as any).duplicateCount = 0;
    WebSocket.prototype.send = function (data) {
      if (!sockets.has(this)) {
        sockets.add(this);
        const receive = this.onmessage;
        this.onmessage = function (event) {
          receive?.call(this, event);
          let frame;
          try {
            frame = JSON.parse(String(event.data));
          } catch {
            return;
          }
          if (frame.method && typeof frame.params?.seq === 'number') {
            (window as any).duplicateCount++;
            // Delayed stale deliveries must not duplicate transcript/tool state.
            setTimeout(() => receive?.call(this, event), 15);
          }
        };
      }
      return originalSend.call(this, data);
    };
    (window as any).disconnectAuditSockets = () => {
      for (const socket of sockets) {
        if (socket.readyState === WebSocket.OPEN) {
          socket.close(4001, 'three-session audit');
        }
      }
    };
  });
  const composer = visibleComposer(page);
  await expect(composer).toBeVisible({ timeout: 90_000 });
  const prompts = ['TILE_UI_A WAIT_FOR_SESSION_A', 'TILE_QUESTION_B', 'TILE_APPROVAL_C'];
  const ids: string[] = [];
  for (let index = 0; index < 3; index++) {
    if (index > 0) {
      await newDeckChat(page);
      await expect.poll(() => activeSessionId(page)).not.toBe(ids[index - 1]);
    }
    ids.push(await activeSessionId(page));
    await composer.fill(prompts[index]!);
    await composer.press('Enter');
  }
  const columns = ids.map((id) => sessionPane(page, id));
  const [a, b, c] = columns;
  const show = (owner: number) => openSession(page, prompts[owner]!);
  await show(0);
  await expect(a!.getByText('A_ARTIFACT_initial', { exact: true })).toBeVisible({ timeout: 240_000 });
  await show(1);
  await expect(b!.getByText('Question for tile B', { exact: true })).toBeVisible({ timeout: 90_000 });
  await show(2);
  await expect(c!.getByRole('button', { name: 'Approve Once', exact: true })).toBeVisible({ timeout: 90_000 });
  for (let index = 0; index < 3; index++) {
    await show(index);
    await composer.fill(`Owned draft ${index}`);
    await columns[index]!.locator('input[type="file"]').setInputFiles({
      name: `tile-${index}.txt`,
      mimeType: 'text/plain',
      buffer: Buffer.from(`Only tile ${index}`),
    });
  }
  const checkOwners = async () => {
    for (let owner = 0; owner < 3; owner++) {
      await show(owner);
      await expect(composer).toHaveValue(`Owned draft ${owner}`);
      await expect(columns[owner]!.getByText(`tile-${owner}.txt`, { exact: true }).first()).toBeVisible();
    }
  };
  let seed = 0x514c;
  for (let step = 0; step < 18; step++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const index = seed % 3;
    await show(index);
    // focus() silently does nothing on a disabled reconnecting composer.
    // A real user cannot select that field until the connection is ready.
    await expect(async () => {
      await expect(composer).toBeEditable();
      await composer.click();
      await expect(composer).toBeFocused();
    }).toPass({ timeout: 90_000 });
    if (step % 3 === 2) {
      await page.evaluate(() => (window as any).disconnectAuditSockets());
    }
    await checkOwners();
  }
  expect(await page.evaluate(() => (window as any).duplicateCount)).toBeGreaterThan(0);
  await page.context().setOffline(true);
  try {
    await page.evaluate(() => (window as any).disconnectAuditSockets());
    for (let owner = 0; owner < 3; owner++) {
      await show(owner);
      await expect(composer).toBeDisabled({ timeout: 30_000 });
    }
  } finally {
    await page.context().setOffline(false);
  }
  for (let owner = 0; owner < 3; owner++) {
    await show(owner);
    await expect(composer).toBeEditable({ timeout: 90_000 });
    await expect(composer).toHaveValue(`Owned draft ${owner}`);
    const column = columns[owner]!;
    await expect(column.getByTitle('Choose the model for this conversation', { exact: true })).toContainText(
      'GPT 5.2 E2E'
    );
    await expect(column.getByTitle('Choose reasoning effort for this conversation', { exact: true })).toBeVisible();
    await expect(column.getByTitle('Approvals and task checks for this conversation', { exact: true })).toBeVisible();
  }
  // Questions retain the running phase; approval waits have their own paused
  // phase. The existing control policy locks only thinking/running phases.
  await show(1);
  await expect(b!.getByTitle('Choose the model for this conversation', { exact: true })).toBeDisabled();
  await expect(b!.getByText('Question for tile B', { exact: true })).toBeVisible();
  await show(2);
  await expect(c!.getByTitle('Choose the model for this conversation', { exact: true })).toBeEnabled();
  await expect(c!.getByRole('button', { name: 'Approve Once', exact: true })).toBeVisible();
  await show(0);
  await expect(a!.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);
  await expect(a!.getByTitle('Choose the model for this conversation', { exact: true })).toBeEnabled();
  await expect(a!.getByText('A_ARTIFACT_initial', { exact: true })).toBeVisible();
  await attachProofPng(testInfo, 'three sessions recovered after offline transport', await app.captureScreenshot());
  await page.reload();
  for (let owner = 0; owner < 3; owner++) {
    await show(owner);
    await expect(composer).toHaveValue(`Owned draft ${owner}`, { timeout: 90_000 });
    await expect(columns[owner]!.getByText(`tile-${owner}.txt`, { exact: true }).first()).toBeVisible();
    for (let other = 0; other < 3; other++) {
      if (other !== owner) {
        await expect(columns[owner]!.getByRole('log')).not.toContainText(prompts[other]!);
      }
    }
    await attachProofPng(
      testInfo,
      `recovered owner ${owner} content and uploaded draft`,
      await app.captureScreenshot()
    );
  }
  await show(0);
  await expect(a!.getByText('A_ARTIFACT_initial', { exact: true })).toHaveCount(1);
  await expect(a!.getByText('A_PLAN_initial', { exact: true }).first()).toBeVisible();
  await show(2);
  await c!.getByRole('button', { name: 'Approve Once', exact: true }).click();
  await expect(c!.getByRole('button', { name: 'Approve Once', exact: true })).toHaveCount(0);
  await show(1);
  await expect(b!.getByText('Question for tile B', { exact: true })).toBeVisible();
});
