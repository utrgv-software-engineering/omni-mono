import { expect, test } from 'tests/e2e/fixtures/test';
import { createProject, projectsList } from 'tests/e2e/support/app';

test.describe('persistence', () => {
  test('keeps project state after app restart', async ({ app }) => {
    const projectName = 'E2E Persistent Project';

    await createProject(app.page, projectName);

    const restarted = await app.restart();
    await expect(projectsList(restarted).getByText(projectName, { exact: true })).toBeVisible();
  });
});
