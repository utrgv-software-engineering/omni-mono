import { expect, test } from 'tests/e2e/fixtures/test';
import { openProjects } from 'tests/e2e/support/app';

test.use({ seedState: 'seeded-project' });

test.describe('deterministic seeded state', () => {
  test('loads a seeded project', async ({ appPage }) => {
    await openProjects(appPage);
    await expect(appPage.getByText('Seeded Project').first()).toBeVisible();
  });
});
