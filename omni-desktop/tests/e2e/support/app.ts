import type { Locator, Page } from '@playwright/test';
import { expect } from '@playwright/test';

/** The sidebar's fixed rows (New chat). */
export function sidebarSurfaces(page: Page): Locator {
  return page.getByRole('list', { name: 'Surfaces' });
}

/** The sidebar's project rows. */
export function projectsList(page: Page): Locator {
  return page.getByRole('list', { name: 'Projects' });
}

/** A session row in the sidebar (Recents or a project's sessions). */
export function sessionRow(page: Page, name: string | RegExp): Locator {
  return page
    .getByRole('list', { name: /^(Recents|Project sessions)$/ })
    .getByRole('button', { name, exact: typeof name === 'string' })
    .first();
}

/** Sidebar "New chat": show the chat surface on a fresh (or reused pristine) chat. */
export async function openChat(page: Page): Promise<void> {
  await sidebarSurfaces(page).getByRole('button', { name: 'New chat', exact: true }).click();
  await expect(visibleComposer(page)).toBeVisible({ timeout: 90_000 });
}

/** A chat session's pane, visible or parked (Focus keeps inactive sessions mounted). */
export function sessionPane(page: Page, tabId: string): Locator {
  return page.locator(`[data-deck-column="${tabId}"]`);
}

/** The id of the chat session currently shown. */
export async function activeSessionId(page: Page): Promise<string> {
  const pane = page.locator('[data-deck-column]:not([aria-hidden="true"])');
  await expect(pane).toHaveCount(1);
  return (await pane.getAttribute('data-deck-column'))!;
}

/** Switch the chat surface to a session from the sidebar, by its title. */
export async function openSession(page: Page, title: string | RegExp): Promise<void> {
  const row = sessionRow(page, title);
  await row.click();
  // Wait for the switch to land so later locators don't grab the outgoing pane.
  await expect(row).toHaveAttribute('data-active', 'true');
  await expect(page.locator('[data-deck-column]:not([aria-hidden="true"])')).toHaveCount(1);
}

/**
 * Wait until a chat's run has finished: the session is connected, loaded and
 * idle. `scope` is a session pane, or the page for the chat currently shown.
 * Waiting for the Stop button to vanish is not enough: it also disappears
 * while a reconnecting session reloads its snapshot.
 */
export async function expectRunFinished(scope: Page | Locator, timeout = 90_000): Promise<void> {
  const pane = 'goto' in scope ? scope.locator('[data-deck-column]:not([aria-hidden="true"])') : scope;
  await expect(pane.locator('[data-run-state]')).toHaveAttribute('data-run-state', 'idle', { timeout });
}

/** Deck header "New chat": always opens another chat column. */
export async function newDeckChat(page: Page): Promise<void> {
  await page.getByRole('main').getByRole('button', { name: 'New chat', exact: true }).first().click();
}

export async function openSettings(page: Page): Promise<void> {
  const settingsButton = page.getByRole('list', { name: 'Management' }).getByRole('button', { name: 'Settings' });
  await settingsButton.click();
  await expect(settingsButton).toHaveAttribute('data-active', 'true');
}

async function openMoreItem(page: Page, item: string): Promise<void> {
  await page.getByRole('list', { name: 'Management' }).getByRole('button', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: item, exact: true }).click();
}

export async function openPlugins(page: Page): Promise<void> {
  const heading = page.getByRole('heading', { name: 'Plugins', exact: true });
  if (await heading.isVisible().catch(() => false)) {
    return;
  }
  await openMoreItem(page, 'Plugins');
  await expect(heading).toBeVisible();
}

export async function openRoutines(page: Page): Promise<void> {
  await openMoreItem(page, 'Routines');
  await expect(page.getByRole('main').getByRole('button', { name: 'New routine' }).first()).toBeVisible();
}

function projectRow(page: Page, projectName: string): Locator {
  return projectsList(page).getByRole('listitem').filter({ hasText: projectName }).first();
}

export async function createProject(page: Page, projectName: string): Promise<void> {
  await page.getByRole('button', { name: 'New project' }).click();
  const dialog = page.getByRole('dialog').filter({ hasText: 'New Project' });
  await dialog.getByRole('textbox', { name: 'Project name' }).fill(projectName);
  await dialog.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(projectRow(page, projectName)).toBeVisible();
}

export async function openProject(page: Page, projectName: string): Promise<void> {
  const row = projectRow(page, projectName);
  await row.hover();
  await row.getByRole('button', { name: `Open ${projectName}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: projectName, level: 1 })).toBeVisible();
}

export async function openProjectSettings(page: Page): Promise<void> {
  await page.getByRole('tab', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Connected files' })).toBeVisible();
}

export async function openAddSourceDialog(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Add files' }).click();
  await expect(page.getByRole('dialog').filter({ hasText: 'Add source' })).toBeVisible();
}

export async function fillGitSource(page: Page, repoUrl: string, mountName: string): Promise<void> {
  await page.getByRole('combobox', { name: 'Source type' }).selectOption('url');
  await page.getByRole('textbox', { name: 'Repo URL' }).fill(repoUrl);
  await page.getByRole('textbox', { name: 'Source mount name' }).fill(mountName);
}

export async function addGitSource(page: Page, repoUrl: string, mountName: string): Promise<void> {
  await openAddSourceDialog(page);
  await fillGitSource(page, repoUrl, mountName);
  await page.getByRole('button', { name: 'Add source' }).last().click();
  await expect(page.getByRole('button', { name: `${mountName} actions` })).toBeVisible();
}

export async function openSourceEditor(page: Page, mountName: string): Promise<void> {
  await page.getByRole('button', { name: `${mountName} actions` }).click();
  await page.getByRole('menuitem', { name: 'Edit connection' }).click();
  await expect(page.getByRole('dialog').filter({ hasText: 'Edit source' })).toBeVisible();
}

/** The chat composer of the visible chat column (Focus keeps other columns mounted but hidden). */
export function visibleComposer(page: Page): Locator {
  return page.getByPlaceholder('How can I help you today?').filter({ visible: true });
}

/** The Files app's tree, opened if the sidecar starts with it collapsed. */
export async function workspaceFileTree(filesSurface: Locator): Promise<Locator> {
  const showTree = filesSurface.getByRole('button', { name: 'Show file tree', exact: true });
  if (await showTree.isVisible()) {
    await showTree.click();
  }
  return filesSurface.getByRole('tree', { name: 'Workspace files' });
}

/** A file-tree row by its label (rows also carry their actions button's name). */
export function fileTreeItem(tree: Locator, label: string): Locator {
  return tree.getByRole('treeitem', {
    name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} Actions for `),
  });
}
