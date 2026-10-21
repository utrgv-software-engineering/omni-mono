import { expect, test } from 'tests/e2e/fixtures/test';
import { openRoutines, openSettings, openSpaces, projectsList, sidebarSurfaces } from 'tests/e2e/support/app';

test.describe('app shell', () => {
  test('loads the sidebar and switches core surfaces', async ({ appPage, mode }) => {
    await test.step(`verify ${mode} sidebar`, async () => {
      await expect(sidebarSurfaces(appPage).getByRole('button', { name: 'New chat', exact: true })).toBeVisible();
      await expect(sidebarSurfaces(appPage).getByRole('button', { name: 'Spaces', exact: true })).toBeVisible();
      await expect(projectsList(appPage)).toBeVisible();
      await expect(appPage.getByRole('list', { name: 'Management' })).toBeVisible();
    });

    await test.step('switch to Spaces', async () => {
      await openSpaces(appPage);
      await expect(appPage.getByRole('radio', { name: 'Spaces', exact: true })).toBeChecked();
    });

    await test.step('switch to Settings', async () => {
      await openSettings(appPage);
      await expect(appPage.getByRole('heading', { name: 'Settings', level: 2 })).toBeVisible();
    });

    await test.step('switch to Routines', async () => {
      await openRoutines(appPage);
    });
  });
});
