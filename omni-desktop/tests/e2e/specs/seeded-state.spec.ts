import { expect, test } from 'tests/e2e/fixtures/test';
import { projectsList } from 'tests/e2e/support/app';

test.use({ seedState: 'seeded-project' });

test.describe('deterministic seeded state', () => {
  test('loads a seeded project', async ({ appPage }) => {
    await expect(projectsList(appPage).getByText('Seeded Project', { exact: true })).toBeVisible();
  });
});
