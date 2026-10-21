import { expect, test } from 'tests/e2e/fixtures/test';
import { openChat, openPlugins, openSpaces, visibleComposer } from 'tests/e2e/support/app';

test.describe('surfaces availability', () => {
  test('keeps primary user surfaces reachable and mode-appropriate', async ({ appPage, mode }) => {
    await openChat(appPage);
    await expect(visibleComposer(appPage)).toBeVisible();

    await openSpaces(appPage);
    await expect(appPage.getByRole('radio', { name: 'Spaces', exact: true })).toBeChecked();

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
