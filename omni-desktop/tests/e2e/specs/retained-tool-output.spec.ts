import { writeFile } from 'node:fs/promises';
import path from 'node:path';

import { expect, test } from 'tests/e2e/fixtures/test';

test.use({ seedState: 'lazy-host-first-message' });

test('retrieves omitted text from saved tool output after restart', async ({ app }, testInfo) => {
  test.setTimeout(360_000);
  await writeFile(
    path.join(app.workspaceDir, 'oversized-output.txt'),
    `${'A'.repeat(90_000)}RETAINED_MIDDLE_NEEDLE${'B'.repeat(90_000)}`
  );
  let page = app.page;
  await expect(page.getByPlaceholder('How can I help you today?')).toBeVisible({ timeout: 90_000 });
  await page
    .getByPlaceholder('How can I help you today?')
    .fill(`RETAINED_OUTPUT_PROOF ${path.join(app.workspaceDir, 'oversized-output.txt')}`);
  await page.getByPlaceholder('How can I help you today?').press('Enter');
  await expect(page.getByText('HOST_FIRST_MESSAGE_READY', { exact: true }).last()).toBeVisible({ timeout: 240_000 });
  // Tool details live inside the activity disclosure.
  const openOutput = async () => {
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);
    const activity = page.getByRole('button', { name: '1 step — 1 read', exact: true });
    await expect(activity).toBeVisible();
    // Completed activities collapse automatically; wait for that transition.
    await expect(activity).toHaveAttribute('aria-expanded', 'false');
    await activity.click();
    const read = page.getByRole('button', { name: 'Read saved output', exact: true });
    if (!(await read.isVisible())) {
      await page
        .getByRole('button', { name: /Read.*line|Read.*oversized-output|Read file|read_file/i })
        .last()
        .click();
    }
    await read.click();
    await expect(page.getByText('Showing text from character 0.', { exact: true })).toBeVisible();
    await page.getByLabel('Find in saved output').fill('RETAINED_MIDDLE_NEEDLE');
    await page.getByRole('button', { name: 'Find', exact: true }).click();
    await expect(page.locator('section[aria-label="Saved tool output"] pre')).toContainText('RETAINED_MIDDLE_NEEDLE');
  };
  await openOutput();
  await testInfo.attach('saved-output-search', {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  });
  page = await app.restart();
  await page
    .getByRole('list', { name: 'Recents', exact: true })
    .getByRole('button', { name: /^RETAINED_OUTPUT_PROOF/ })
    .first()
    .click();
  await expect(page.getByText('HOST_FIRST_MESSAGE_READY', { exact: true }).last()).toBeVisible({ timeout: 90_000 });
  await openOutput();
});
