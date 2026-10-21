import { expect, test } from 'tests/e2e/fixtures/test';
import { openSettings } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'provider-switch' });

test('changes the workspace provider only after confirmation and persists it', async ({ app }, info) => {
  test.setTimeout(240_000);
  const page = app.page;
  await expect(page.getByRole('button', { name: 'Settings', exact: true })).toBeVisible();
  await openSettings(page);
  await page.getByRole('button', { name: 'AI', exact: true }).click();
  const provider = page.getByLabel('Provider', { exact: true });
  await expect(provider).toHaveValue('sandbox');
  await expect(
    page.getByText('All projects, residents, subagents, reviews, and recaps use this provider.')
  ).toBeVisible();
  await expect(page.getByLabel('Default model').locator('option[value^="codex/"]')).toHaveCount(0);
  await expect(provider.locator('option[value="second"]')).toHaveCount(1, { timeout: 120_000 });
  await provider.selectOption('second');
  await expect(page.getByLabel('Default model')).not.toHaveValue(/sandbox\//);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  const confirmation = page.getByRole('alertdialog');
  await expect(confirmation).toContainText('Continuing existing conversations will send their history to second');
  await attachProofPng(info, 'explicit provider change disclosure', await app.captureScreenshot());
  await confirmation.getByRole('button', { name: 'Cancel', exact: true }).click();
  // Cancellation must not persist the pending provider.
  await app.restart();
  await expect(app.page.getByRole('button', { name: 'Settings', exact: true })).toBeVisible();
  await openSettings(app.page);
  await app.page.getByRole('button', { name: 'AI', exact: true }).click();
  await expect(app.page.getByLabel('Provider', { exact: true })).toHaveValue('sandbox');
  await app.page.getByLabel('Provider', { exact: true }).selectOption('second');
  await app.page.getByRole('button', { name: 'Save', exact: true }).click();
  await app.page.getByRole('alertdialog').getByRole('button', { name: 'Change provider', exact: true }).click();
  await expect(app.page.getByText('Unsaved changes', { exact: true })).toHaveCount(0);
  await expect(app.page.getByRole('button', { name: 'Saving…', exact: true })).toHaveCount(0, { timeout: 120_000 });
  await expect(app.page.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0);
  await attachProofPng(info, 'workspace provider saved', await app.captureScreenshot());
  await app.restart();
  await expect(app.page.getByRole('button', { name: 'Settings', exact: true })).toBeVisible();
  await openSettings(app.page);
  await app.page.getByRole('button', { name: 'AI', exact: true }).click();
  await expect(app.page.getByLabel('Provider', { exact: true })).toHaveValue('second');
  // A terminal change must survive desktop/server startup, even though their
  // legacy settings seed still selects the original provider.
  app.setProviderFromCli('sandbox/gpt-5.2');
  await app.restart();
  await expect(app.page.getByRole('button', { name: 'Settings', exact: true })).toBeVisible();
  await openSettings(app.page);
  await app.page.getByRole('button', { name: 'AI', exact: true }).click();
  await expect(app.page.getByLabel('Provider', { exact: true })).toHaveValue('sandbox');
  await attachProofPng(info, 'CLI provider survives app restart', await app.captureScreenshot());
});
