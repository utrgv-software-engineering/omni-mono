import type { Page } from '@playwright/test';
import { expect } from '@playwright/test';

export async function openProjects(page: Page): Promise<void> {
  await page.getByRole('tab', { name: 'Projects' }).click();
  await expect(page.getByRole('tab', { name: 'Projects' })).toHaveAttribute('aria-selected', 'true');
}

export async function openChat(page: Page): Promise<void> {
  await page.getByRole('tab', { name: 'Chat' }).click();
  await expect(page.getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true');
}

export async function openCode(page: Page): Promise<void> {
  const codeTab = page.getByRole('tab', { name: 'Code' });
  const spacesTab = page.getByRole('tab', { name: 'Spaces' });
  if (await codeTab.count()) {
    await codeTab.click();
    await expect(codeTab).toHaveAttribute('aria-selected', 'true');
  } else {
    await spacesTab.click();
    await expect(spacesTab).toHaveAttribute('aria-selected', 'true');
  }
}

export async function openSettings(page: Page): Promise<void> {
  const settingsButton = page.getByRole('button', { name: 'Settings', exact: true });
  if (await settingsButton.count()) {
    await settingsButton.click();
    await expect(settingsButton).toHaveAttribute('data-active', 'true');
    return;
  }

  const settingsTab = page.getByRole('tab', { name: 'Settings' });
  await settingsTab.click();
  await expect(settingsTab).toHaveAttribute('aria-selected', 'true');
}

export async function openPlugins(page: Page): Promise<void> {
  const heading = page.getByRole('heading', { name: 'Plugins', exact: true });
  if (await heading.isVisible().catch(() => false)) {
    return;
  }
  await page.getByRole('button', { name: 'More', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Plugins', exact: true }).click();
  await expect(heading).toBeVisible();
}

export async function openRoutines(page: Page): Promise<void> {
  await page.getByRole('tab', { name: 'Routines' }).click();
  await expect(page.getByRole('tab', { name: 'Routines' })).toHaveAttribute('aria-selected', 'true');
}

export async function createProject(page: Page, projectName: string): Promise<void> {
  await openProjects(page);
  await page.getByRole('button', { name: 'New project' }).first().click();
  await page.getByRole('textbox', { name: 'Project name' }).fill(projectName);
  await page.getByRole('button', { name: 'Create Project' }).click();
  await expect(projectTree(page).getByRole('treeitem', { name: new RegExp(projectName) })).toBeVisible();
}

export async function openProject(page: Page, projectName: string): Promise<void> {
  await openProjects(page);
  const project = projectTree(page)
    .getByRole('treeitem', { name: new RegExp(projectName) })
    .first();
  await project.click();
  await expect(project).toBeVisible();
}

export function projectTree(page: Page) {
  return page.getByRole('tree', { name: 'Project tree' });
}

export async function openAddSourceDialog(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Project actions' }).last().click();
  await page.getByRole('menuitem', { name: 'Add source…' }).click();
  await expect(page.getByRole('dialog').filter({ hasText: 'Add source' })).toBeVisible();
}

export async function addGitSource(page: Page, repoUrl: string, mountName: string): Promise<void> {
  await openAddSourceDialog(page);
  await page.getByRole('combobox', { name: 'Source type' }).selectOption('url');
  await page.getByRole('textbox', { name: 'Repo URL' }).fill(repoUrl);
  await page.getByRole('textbox', { name: 'Source mount name' }).fill(mountName);
  await page.getByRole('button', { name: 'Add source' }).last().click();
  await expect(projectTree(page).getByRole('treeitem', { name: /Sources \(1\)/ })).toBeVisible();
}
