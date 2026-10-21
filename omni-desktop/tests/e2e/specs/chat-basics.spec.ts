import { expect, test } from 'tests/e2e/fixtures/test';
import { openChat, visibleComposer } from 'tests/e2e/support/app';

test.describe('chat basics', () => {
  test('lets a user compose a message and preserves a safe chat surface after restart', async ({ app }) => {
    const prompt = `E2E chat prompt ${Date.now()}`;

    await openChat(app.page);
    await expect(visibleComposer(app.page)).toBeVisible();
    await expect(app.page.getByLabel('Attach files').filter({ visible: true })).toBeVisible();

    await visibleComposer(app.page).fill(prompt);
    await expect(app.page.getByRole('button', { name: 'Send' }).filter({ visible: true })).toBeEnabled();
    await app.page.getByRole('button', { name: 'Send' }).filter({ visible: true }).click();
    await expect(visibleComposer(app.page)).toHaveValue('');

    const restarted = await app.restart();
    await openChat(restarted);
    await expect(visibleComposer(restarted)).toBeVisible();
  });
});
