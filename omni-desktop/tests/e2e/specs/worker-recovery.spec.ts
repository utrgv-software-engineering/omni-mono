import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

import { expect, test } from 'tests/e2e/fixtures/test';

test.use({ seedState: 'lazy-host-first-message' });

test('recovers an interrupted worker after abrupt runtime death and resumes its original workspace', async ({
  app,
}, testInfo) => {
  test.setTimeout(360_000);
  const sessionRoot = path.join(
    app.workspaceDir,
    'Sessions',
    `session-${path.basename(path.dirname(app.workspaceDir))}`
  );
  await mkdir(sessionRoot, { recursive: true });
  await writeFile(path.join(sessionRoot, 'worker-input.txt'), 'WORKER_PERSISTED_INPUT');
  const page = app.page;
  const prompt = page.getByRole('textbox', { name: 'How can I help you today?' });
  await expect(prompt).toBeVisible({ timeout: 90_000 });
  await prompt.fill('WORKER_RECOVERY_PROOF');
  await prompt.press('Enter');
  await expect(page.getByRole('button', { name: 'Approve Once', exact: true })).toBeVisible({ timeout: 90_000 });
  await page.getByRole('button', { name: 'Approve Once', exact: true }).click();
  await expect(page.getByText('WORKER_PARENT_READY', { exact: true }).last()).toBeVisible({ timeout: 120_000 });
  await expect
    .poll(
      async () => {
        const approval = page.getByRole('button', { name: 'Approve Once', exact: true });
        if (await approval.isVisible()) {
          await approval.click();
        }
        return app.inspectModelRequests().some((request) => request.marker === 'WORKER_CHILD_RECORDED');
      },
      {
        timeout: 90_000,
      }
    )
    .toBe(true);
  // A genuine process-tree kill; no graceful worker-finalization callback runs.
  const restored = await app.restart({ crash: true });
  await restored
    .getByRole('list', { name: 'Recents', exact: true })
    .getByRole('button', { name: /^WORKER_RECOVERY_PROOF/ })
    .first()
    .click();
  await expect(restored.getByText('WORKER_PARENT_READY', { exact: true }).last()).toBeVisible({ timeout: 90_000 });
  await restored.getByRole('radio', { name: 'Agents', exact: true }).first().click();
  await restored
    .getByText('WORKER_CHILD_HOLD Read worker-input.txt and retain the result.', { exact: true })
    .last()
    .click();
  await expect(restored.getByRole('button', { name: 'Resume worker', exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(restored.getByText('Worker interrupted', { exact: true })).toBeVisible();
  await testInfo.attach('interrupted-worker', {
    body: await restored.screenshot({ fullPage: true }),
    contentType: 'image/png',
  });
  await restored.getByRole('button', { name: 'Resume worker', exact: true }).click();
  await expect
    .poll(
      async () => {
        const approval = restored.getByRole('button', { name: 'Approve Once', exact: true });
        if (await approval.isVisible()) {
          await approval.click();
        }
        return restored.getByText('WORKER_RESUMED_READY', { exact: true }).last().isVisible();
      },
      { timeout: 120_000 }
    )
    .toBe(true)
    .catch(async (error) => {
      await testInfo.attach('model-requests', {
        body: JSON.stringify(app.inspectModelRequests()),
        contentType: 'application/json',
      });
      throw error;
    });
  await expect
    .poll(() => app.inspectModelRequests().some((request) => request.marker === 'WORKER_PARENT_MESSAGE'), {
      timeout: 30_000,
    })
    .toBe(true);
  await expect
    .poll(
      async () => {
        const approval = restored.getByRole('button', { name: 'Approve Once', exact: true });
        if (await approval.isVisible()) {
          await approval.click();
        }
        return app.inspectModelRequests().some((request) => request.marker === 'WORKER_FOLLOWUP_DELIVERED');
      },
      { timeout: 90_000 }
    )
    .toBe(true);
  await expect(restored.getByText('WORKER_FOLLOWUP_READY', { exact: true }).last()).toBeVisible();
  await testInfo.attach('recovered-worker', {
    body: await restored.screenshot({ fullPage: true }),
    contentType: 'image/png',
  });
});
