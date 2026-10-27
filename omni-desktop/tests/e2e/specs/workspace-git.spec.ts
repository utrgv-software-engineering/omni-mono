import { execFileSync } from 'node:child_process';

import type { Page } from '@playwright/test';
import { expect, test } from 'tests/e2e/fixtures/test';
import { attachProofScreenshot } from 'tests/e2e/support/proof';

test.use({ seedState: 'workspace-git' });

async function openSourceControl(page: Page) {
  const seededSession = page.getByRole('button', { name: 'Canonical workspace thread', exact: true });
  await expect(seededSession).toBeVisible({ timeout: 120_000 });
  await seededSession.click();

  const gitButton = page.getByRole('radio', { name: 'Git', exact: true });
  await expect(gitButton).toBeVisible({ timeout: 120_000 });
  await gitButton.click();

  const sourceControl = page.getByRole('region', { name: 'Source control', exact: true });
  await expect(sourceControl).toBeVisible({ timeout: 90_000 });
  // One repository in the workspace: no repository picker.
  await expect(sourceControl.getByRole('radiogroup', { name: 'Diff view' })).toBeVisible({ timeout: 90_000 });
  await expect(sourceControl.getByRole('combobox', { name: 'Repository' })).toHaveCount(0);
  return sourceControl;
}

function stagedPaths(workspaceDir: string): string {
  return execFileSync('git', ['diff', '--cached', '--name-only'], { cwd: workspaceDir, encoding: 'utf-8' }).trim();
}

function latestCommitSubject(workspaceDir: string): string {
  return execFileSync('git', ['log', '-1', '--format=%s'], { cwd: workspaceDir, encoding: 'utf-8' }).trim();
}

test.describe('workspace source control', () => {
  test('keeps change review in the repository-scoped Git app', async ({ app }, testInfo) => {
    test.setTimeout(240_000);
    const sourceControl = await openSourceControl(app.page);

    await app.page.getByRole('button', { name: 'Session menu' }).click();
    await expect(app.page.getByRole('menuitem', { name: 'Changes', exact: true })).toHaveCount(0);
    await app.page.keyboard.press('Escape');

    await expect(sourceControl.getByRole('radio', { name: 'Working tree', exact: true })).toBeChecked();
    await expect(sourceControl.getByRole('region', { name: 'Changes in src/index.ts, hunk 1' })).toBeVisible();
    await expect(sourceControl.getByRole('button', { name: 'Apply to local folder', exact: true })).toHaveCount(0);
    await attachProofScreenshot(sourceControl, testInfo, 'repository-scoped source control');
  });

  test('reviews one changed file and stages and unstages that exact file', async ({ app }, testInfo) => {
    test.setTimeout(240_000);
    const sourceControl = await openSourceControl(app.page);

    const hunk = sourceControl.getByRole('region', { name: 'Changes in src/index.ts, hunk 1' });
    await expect(hunk).toContainText("export const target = 'after';");
    await attachProofScreenshot(sourceControl, testInfo, 'working tree diff');

    await sourceControl.getByRole('button', { name: 'Stage src/index.ts', exact: true }).click();
    await expect.poll(() => stagedPaths(app.workspaceDir)).toBe('src/index.ts');

    await sourceControl.getByRole('radio', { name: 'Staged', exact: true }).click();
    const unstage = sourceControl.getByRole('button', { name: 'Unstage src/index.ts', exact: true });
    await expect(unstage).toBeVisible();
    await attachProofScreenshot(sourceControl, testInfo, 'exact file staged');

    await unstage.click();
    await expect.poll(() => stagedPaths(app.workspaceDir)).toBe('');
    await sourceControl.getByRole('radio', { name: 'Working tree', exact: true }).click();
    await expect(hunk).toBeVisible();
    await attachProofScreenshot(sourceControl, testInfo, 'exact file unstaged');
  });

  test('opens a changed file from the diff in the native Files editor', async ({ app }, testInfo) => {
    test.setTimeout(240_000);
    const page = app.page;
    const sourceControl = await openSourceControl(page);
    await attachProofScreenshot(sourceControl, testInfo, 'git diff before opening the file');

    await sourceControl.getByRole('button', { name: 'Open src/index.ts', exact: true }).click();

    const filesSurface = page.getByRole('region', { name: 'Workspace files', exact: true });
    await expect(filesSurface).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Editor for src/index.ts' })).toContainText(
      "export const target = 'after';"
    );
    await attachProofScreenshot(filesSurface, testInfo, 'changed file opened in Files');
  });

  test('commits staged work and refreshes canonical repository history', async ({ app }, testInfo) => {
    test.setTimeout(240_000);
    const sourceControl = await openSourceControl(app.page);

    await sourceControl.getByRole('button', { name: 'Stage src/index.ts', exact: true }).click();
    await expect.poll(() => stagedPaths(app.workspaceDir)).toBe('src/index.ts');

    const showChanges = sourceControl.getByRole('button', { name: 'Show changes list', exact: true });
    if (await showChanges.isVisible()) {
      await showChanges.click();
    }
    const commitMessage = 'exercise v2 Git actions';
    await sourceControl.getByRole('textbox', { name: 'Commit message' }).fill(commitMessage);
    await attachProofScreenshot(sourceControl, testInfo, 'v2 repository commit ready');
    await sourceControl.getByRole('button', { name: 'Commit', exact: true }).click();

    await expect(sourceControl.getByRole('status').filter({ hasText: 'Changes committed.' })).toBeVisible();
    await expect.poll(() => latestCommitSubject(app.workspaceDir)).toBe(commitMessage);
    await expect.poll(() => stagedPaths(app.workspaceDir)).toBe('');

    await sourceControl.getByRole('button', { name: 'Repository tools', exact: true }).click();
    await sourceControl.getByRole('tab', { name: 'History', exact: true }).click();
    await expect(sourceControl.getByText(commitMessage, { exact: true })).toBeVisible();
    await expect(sourceControl.getByText('fixture baseline', { exact: true })).toBeVisible();
    await attachProofScreenshot(sourceControl, testInfo, 'v2 repository history refreshed');
  });
});
