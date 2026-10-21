import { expect, test } from 'tests/e2e/fixtures/test';
import {
  createProject,
  fillGitSource,
  openAddSourceDialog,
  openProject,
  openProjectSettings,
  openSourceEditor,
} from 'tests/e2e/support/app';

test.describe('read-only project sources', () => {
  test('keeps a source read-only after editing and restart', async ({ app }) => {
    const projectName = 'E2E Read-only Source';

    await createProject(app.page, projectName);
    await openProject(app.page, projectName);
    await openProjectSettings(app.page);
    await openAddSourceDialog(app.page);
    await fillGitSource(app.page, 'https://example.com/acme/reference.git', 'reference');
    await app.page.getByRole('checkbox', { name: 'Read-only source' }).check();
    await app.page.getByRole('button', { name: 'Add source' }).last().click();

    await openSourceEditor(app.page, 'reference');
    await expect(app.page.getByRole('checkbox', { name: 'Read-only source' })).toBeChecked();
    await app.page.getByRole('button', { name: 'Cancel' }).click();

    const restarted = await app.restart();
    await openProject(restarted, projectName);
    await openProjectSettings(restarted);
    await openSourceEditor(restarted, 'reference');
    await expect(restarted.getByRole('checkbox', { name: 'Read-only source' })).toBeChecked();
  });
});
