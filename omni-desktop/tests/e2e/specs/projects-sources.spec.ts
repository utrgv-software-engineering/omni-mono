import { expect, test } from 'tests/e2e/fixtures/test';
import {
  addGitSource,
  createProject,
  fillGitSource,
  openAddSourceDialog,
  openProject,
  openProjectSettings,
} from 'tests/e2e/support/app';

test.describe('projects and sources', () => {
  test('creates a project and adds a git source from the UI', async ({ appPage }) => {
    const projectName = 'E2E Source Project';

    await createProject(appPage, projectName);
    await openProject(appPage, projectName);
    await openProjectSettings(appPage);
    await addGitSource(appPage, 'https://github.com/example/e2e-source-project.git', 'e2e-source-project');

    await test.step('reject duplicate source metadata', async () => {
      await openAddSourceDialog(appPage);
      await fillGitSource(appPage, 'https://github.com/example/e2e-source-project.git', 'e2e-source-project');
      await appPage.getByRole('button', { name: 'Add source' }).last().click();
      await expect(appPage.getByRole('alert')).toBeVisible();
    });
  });
});
