import { expect, test } from 'tests/e2e/fixtures/test';
import { openChat, openPlugins, openRoutines, openSettings, visibleComposer } from 'tests/e2e/support/app';

test.describe('surfaces availability', () => {
  test('keeps primary user surfaces reachable', async ({ appPage }) => {
    await openChat(appPage);
    await expect(visibleComposer(appPage)).toBeEditable({ timeout: 90_000 });

    await openPlugins(appPage);
    await expect(appPage.getByRole('button', { name: 'Browse a marketplace' })).toBeVisible();

    await openRoutines(appPage);

    await openSettings(appPage);
    await expect(appPage.getByRole('heading', { name: 'Settings', level: 2 })).toBeVisible();
  });
});
