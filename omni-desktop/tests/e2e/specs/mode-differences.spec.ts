import { expect, test } from 'tests/e2e/fixtures/test';
import { openPlugins } from 'tests/e2e/support/app';

test.describe('mode-specific UI affordances', () => {
  test('shows desktop-only skill install affordance only in Electron', async ({ appPage, mode }) => {
    await openPlugins(appPage);
    await expect(appPage.getByRole('button', { name: 'Browse a marketplace' })).toBeVisible();

    // The local-file picker is desktop-only: the server has no client filesystem.
    const installFromFile = appPage.getByRole('button', { name: 'Install skill from file' });
    if (mode === 'electron-local') {
      await expect(installFromFile).toBeVisible();
    } else {
      await expect(installFromFile).toHaveCount(0);
    }
  });
});
