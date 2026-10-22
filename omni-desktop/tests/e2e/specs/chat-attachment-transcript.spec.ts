import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';

test.use({ seedState: 'lazy-host-first-message' });

// 64x48 red PNG.
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAEAAAAAwCAIAAAAuKetIAAAAT0lEQVR4nO3PQQ0AIBDAMMC/50MEj4ZkVbDtWX87OuBVA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oD2gX6dwFfoRCNAAAAAABJRU5ErkJggg==',
  'base64'
);

test('a sent image shows as a thumbnail in the transcript, live and after reload', async ({ app }) => {
  test.setTimeout(240_000);
  const page = app.page;
  const composer = visibleComposer(page);
  await expect(composer).toBeVisible({ timeout: 90_000 });
  const pane = sessionPane(page, await activeSessionId(page));
  await page
    .locator('input[type="file"]')
    .first()
    .setInputFiles({ name: 'red.png', mimeType: 'image/png', buffer: png });
  await composer.fill('ATTACHMENT_TRANSCRIPT');
  await composer.press('Enter');
  const log = pane.getByRole('log');
  await expect(log.getByText('QUEUED_IMAGE_RECEIVED', { exact: true })).toBeVisible({ timeout: 120_000 });
  const thumbnail = log.locator('img[src^="data:image/"]');
  await expect(thumbnail).toHaveCount(1);
  await page.reload();
  await openSession(page, 'ATTACHMENT_TRANSCRIPT');
  await expect(log.getByText('QUEUED_IMAGE_RECEIVED', { exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(thumbnail).toHaveCount(1);
});
