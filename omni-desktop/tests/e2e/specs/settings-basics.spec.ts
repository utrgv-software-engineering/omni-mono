import { expect, test } from 'tests/e2e/fixtures/test';
import { openSettings } from 'tests/e2e/support/app';

test.describe('settings basics', () => {
  test('exposes core configuration areas without leaking seeded secrets', async ({ app }) => {
    await openSettings(app.page);

    const nav = app.page.getByRole('main').getByRole('navigation');
    for (const section of ['General', 'AI', 'Appearance', 'Voice & Audio', 'Workspace', 'Environment', 'Git']) {
      await test.step(`open ${section}`, async () => {
        const button = nav.getByRole('button', { name: section, exact: true });
        await button.click();
        await expect(button).toHaveAttribute('data-active', 'true');
      });
    }

    await test.step('raw seeded secrets are not rendered', async () => {
      await expect(app.page.getByText('test-key', { exact: true })).toHaveCount(0);
      await expect(
        app.page.getByText(process.env.OPENAI_API_KEY ?? process.env.SANDBOX_OPENAI_API_KEY ?? 'test-key')
      ).toHaveCount(0);
    });

    const restarted = await app.restart();
    await openSettings(restarted);
    const restartedNav = restarted.getByRole('main').getByRole('navigation');
    await expect(restartedNav.getByRole('button', { name: 'AI', exact: true })).toBeVisible();
    await expect(restartedNav.getByRole('button', { name: 'Git', exact: true })).toBeVisible();
  });
});
