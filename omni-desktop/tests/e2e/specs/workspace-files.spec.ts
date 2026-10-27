import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from 'tests/e2e/fixtures/test';
import { fileTreeItem, sessionRow, workspaceFileTree } from 'tests/e2e/support/app';
import { attachProofScreenshot } from 'tests/e2e/support/proof';

test.use({ seedState: 'workspace-files' });

test.describe('workspace files', () => {
  test('opens, edits, and explicitly saves a workspace file', async ({ app }, testInfo) => {
    test.setTimeout(180_000);
    const page = app.page;
    const targetPath = path.join(app.workspaceDir, 'src', 'index.ts');
    const updatedContent = "export const greeting = 'updated';\r\nconsole.log(greeting);\r\n";

    const seededSession = sessionRow(page, 'Workspace Files chat');
    await expect(seededSession).toBeVisible({ timeout: 120_000 });
    await seededSession.click();

    const filesButton = page.getByRole('radio', { name: 'Files', exact: true }).filter({ visible: true });
    await expect(filesButton).toBeVisible({ timeout: 120_000 });
    await filesButton.click();

    const filesSurface = page.locator('section[aria-label="Workspace files"]');
    await expect(filesSurface).toBeVisible({ timeout: 90_000 });
    const tree = await workspaceFileTree(filesSurface);
    await expect(tree).toBeVisible({ timeout: 90_000 });
    await attachProofScreenshot(filesSurface, testInfo, 'workspace files loaded');

    const sourceFolder = fileTreeItem(tree, 'src');
    await expect(sourceFolder).toBeVisible();
    await sourceFolder.focus();
    await page.keyboard.press('ArrowRight');

    const sourceFile = fileTreeItem(tree, 'index.ts');
    await expect(sourceFile).toBeVisible();
    await sourceFile.click();

    const editor = page.getByRole('textbox', { name: 'Editor for src/index.ts' });
    await expect(editor).toBeVisible();
    await expect(filesSurface.getByRole('status').filter({ hasText: /^Saved$/ })).toBeVisible();

    await editor.click();
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.insertText("export const greeting = 'updated';\nconsole.log(greeting);\n");
    await expect(filesSurface.getByRole('status').filter({ hasText: 'Unsaved changes' })).toBeVisible();
    await attachProofScreenshot(filesSurface, testInfo, 'workspace file has unsaved changes');

    await page.keyboard.press('ControlOrMeta+S');
    await expect(filesSurface.getByRole('status').filter({ hasText: /^Saved$/ })).toBeVisible();
    await expect.poll(() => readFileSync(targetPath, 'utf-8')).toBe(updatedContent);
    await attachProofScreenshot(filesSurface, testInfo, 'workspace file saved');
  });
});
