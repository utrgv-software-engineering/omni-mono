import type { Page } from '@playwright/test';
import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, newDeckChat, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

async function archiveShownSession(page: Page): Promise<void> {
  const shown = sessionPane(page, await activeSessionId(page));
  await shown.getByRole('button', { name: 'Session menu', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Archive session', exact: true }).click();
}

const prompts = {
  questions: ['TILE_QUESTION_A', 'TILE_QUESTION_B'],
  approvals: ['TILE_APPROVAL_A', 'TILE_APPROVAL_B'],
  'active-close': ['WAIT_FOR_SESSION_A', 'PROMPT_FOR_SESSION_B'],
} as const;

for (const scenario of ['questions', 'active-close', 'approvals'] as const) {
  test(`isolates sessions during ${scenario}`, async ({ app }, testInfo) => {
    test.setTimeout(360_000);
    const page = app.page;
    const [promptA, promptB] = prompts[scenario];
    const composer = visibleComposer(page);
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

    if (scenario === 'questions') {
      await expect(b.getByText('Question for tile B', { exact: true })).toBeVisible({ timeout: 90_000 });
      await expect(b).not.toContainText('Question for tile A');
      await openSession(page, promptA);
      await expect(a.getByText('Question for tile A', { exact: true })).toBeVisible({ timeout: 90_000 });
      await expect(a).not.toContainText('Question for tile B');
      await composer.fill('Answer only A');
      await composer.press('Enter');
      await expect(a.getByText('HOST_FIRST_MESSAGE_READY', { exact: true }).last()).toBeVisible({ timeout: 90_000 });
      await composer.fill('Surviving A draft');
      await openSession(page, promptB);
      await expect(b.getByText('Question for tile B', { exact: true })).toBeVisible();
      await archiveShownSession(page);
      await expect(b).toHaveCount(0);
      await openSession(page, promptA);
      await expect(composer).toHaveValue('Surviving A draft');
    } else if (scenario === 'approvals') {
      const approveB = b.getByRole('button', { name: 'Approve Once', exact: true });
      await expect(approveB).toBeVisible({ timeout: 90_000 });
      await openSession(page, promptA);
      const approveA = a.getByRole('button', { name: 'Approve Once', exact: true });
      await expect(approveA).toBeVisible({ timeout: 90_000 });
      await page.evaluate(() => {
        const send = WebSocket.prototype.send;
        WebSocket.prototype.send = function (data) {
          let frame;
          try {
            frame = JSON.parse(String(data));
          } catch {}
          if (frame?.method === 'tool_approval_response' && frame.params?.decision === 'approve') {
            WebSocket.prototype.send = send;
            queueMicrotask(() =>
              this.onmessage?.call(
                this,
                new MessageEvent('message', {
                  data: JSON.stringify({
                    jsonrpc: '2.0',
                    id: frame.id,
                    error: { code: -32603, message: 'Injected approval transport failure' },
                  }),
                })
              )
            );
            return;
          }
          send.call(this, data);
        };
      });
      await approveA.click();
      // A failed decision leaves the card actionable with no message.
      await expect(approveA).toBeEnabled();
      await expect(a.getByRole('alert')).toHaveCount(0);
      await attachProofPng(testInfo, 'approval error stays in A', await app.captureScreenshot());
      await approveA.click();
      await expect(approveA).toHaveCount(0);
      await openSession(page, promptB);
      await expect(approveB).toBeEnabled();
      await b.getByRole('button', { name: 'Reject', exact: true }).click();
      await expect(approveB).toHaveCount(0);
    } else {
      await composer.fill('Surviving B draft');
      await openSession(page, promptA);
      await expect(a.getByRole('button', { name: 'Stop', exact: true })).toBeVisible();
      await archiveShownSession(page);
      await expect(a).toHaveCount(0);
      await openSession(page, promptB);
      await expect(b.getByText('SESSION_B_REPLY', { exact: true }).last()).toBeVisible({ timeout: 90_000 });
      await expect(composer).toHaveValue('Surviving B draft');
      await expect(b.getByRole('log')).not.toContainText('SESSION_A_REPLY');
    }
    await attachProofPng(testInfo, scenario, await app.captureScreenshot());
  });
}
