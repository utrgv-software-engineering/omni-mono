import { readFileSync } from 'node:fs';

import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, sessionPane, visibleComposer } from 'tests/e2e/support/app';
import { processTree } from 'tests/e2e/support/process';

test.use({ seedState: 'lazy-host-first-message' });

// A distinctive argv, so only this chat's shell command matches.
const MARKER = '4242.17';

function markedCommands(roots: number[]): number[] {
  return roots
    .flatMap((root) => processTree(root))
    .filter((pid) => {
      try {
        const args = readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').filter(Boolean);
        return args[0]?.endsWith('sleep') === true && args[1] === MARKER;
      } catch {
        return false;
      }
    });
}

test('archiving a chat closes the shells in its terminal', async ({ app }) => {
  test.skip(process.platform !== 'linux', 'Uses /proc to verify the shell exited');
  test.setTimeout(240_000);
  const page = app.page;
  const composer = visibleComposer(page);
  await expect(composer).toBeVisible({ timeout: 90_000 });
  const id = await activeSessionId(page);
  const pane = sessionPane(page, id);
  await composer.fill('TERMINAL_RETIREMENT');
  await composer.press('Enter');
  await expect(pane.getByText('HOST_FIRST_MESSAGE_READY', { exact: true })).toBeVisible({ timeout: 120_000 });

  await page.getByRole('button', { name: 'Show apps', exact: true }).click();
  await page.getByRole('button', { name: 'Terminal', exact: true }).click();
  const terminal = page.locator('.xterm').filter({ visible: true }).first();
  await expect(terminal).toBeVisible({ timeout: 60_000 });
  await terminal.click();
  await page.keyboard.type(`sleep ${MARKER}\n`);
  await expect.poll(() => markedCommands(app.runtimePids()).length, { timeout: 30_000 }).toBe(1);

  await page.getByRole('button', { name: 'Session menu', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Archive session', exact: true }).click();
  await expect.poll(() => markedCommands(app.runtimePids()).length, { timeout: 30_000 }).toBe(0);
});
