import { expect, test } from 'tests/e2e/fixtures/test';
import { openChat, openCode, openSettings } from 'tests/e2e/support/app';

test.describe('surfaces availability', () => {
  test('keeps primary user surfaces reachable and mode-appropriate', async ({ appPage, mode }) => {
    await openChat(appPage);
    await expect(appPage.getByPlaceholder('How can I help you today?')).toBeVisible();

    await openCode(appPage);
    await expect(appPage.getByText(/Spaces|No apps installed|Chat|Browser|Settings/).first()).toBeVisible();

    await openSettings(appPage);
    await appPage.getByRole('button', { name: 'Skills' }).first().click();
    await expect(appPage.getByRole('button', { name: 'Install from marketplace' })).toBeVisible();
    const installFromFile = appPage.getByRole('button', { name: 'Install from file' });
    if (mode === 'electron-local') {
      await expect(installFromFile).toBeVisible();
    } else {
      await expect(installFromFile).toHaveCount(0);
    }
  });
});
