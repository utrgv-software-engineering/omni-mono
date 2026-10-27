import { expect, test } from 'tests/e2e/fixtures/test';
import { openRoutines } from 'tests/e2e/support/app';

test.use({ seedState: 'seeded-project' });

test.describe('routines', () => {
  test('creates, edits, pauses, runs, opens, and deletes a desktop scheduled task', async ({ app }) => {
    const page = app.page;
    await openRoutines(page);

    await test.step('create routine', async () => {
      await expect(page.getByText('No routines yet')).toBeVisible();
      await page.getByRole('main').getByRole('button', { name: 'New routine' }).first().click();
      await page.getByRole('textbox', { name: 'Name' }).fill('E2E morning review');
      await page.getByRole('textbox', { name: 'Instructions' }).fill('Summarize the workspace status.');
      await expect(page.getByRole('combobox', { name: 'Schedule' })).toHaveValue('daily');
      await expect(page.getByText(/Runs ask before using tools/)).toBeVisible();
      await page.getByRole('combobox', { name: 'Project' }).selectOption({ label: 'Seeded Project' });
      await page.getByRole('button', { name: 'Create routine' }).click();

      const properties = page.getByLabel('E2E morning review properties');
      await expect(properties).toBeVisible();
      await expect(properties.getByText(/^Daily at \d\d:\d\d$/)).toBeVisible();
      await expect(properties.getByText('Seeded Project', { exact: true })).toBeVisible();
      await expect(page.getByLabel('E2E morning review recent runs').getByText('No runs yet.')).toBeVisible();
    });

    await test.step('edit routine', async () => {
      await page.getByRole('button', { name: 'Edit', exact: true }).click();
      await expect(page.getByText('Edit routine', { exact: true })).toBeVisible();
      await page.getByRole('textbox', { name: 'Name' }).fill('E2E edited review');
      await page.getByRole('combobox', { name: 'Schedule' }).selectOption('weekly');
      await page.getByLabel('Time').fill('10:30');
      await page.getByRole('combobox', { name: 'Day' }).selectOption('2');
      await page.getByRole('button', { name: 'Save changes' }).click();
      const properties = page.getByLabel('E2E edited review properties');
      await expect(properties).toBeVisible();
      await expect(properties.getByText('Tuesdays at 10:30', { exact: true })).toBeVisible();
    });

    await test.step('pause and resume routine', async () => {
      const properties = page.getByLabel('E2E edited review properties');
      await properties.getByRole('switch').click();
      await expect(properties.getByText('Paused', { exact: true })).toBeVisible();
      await properties.getByRole('switch').click();
      await expect(properties.getByText('Active', { exact: true })).toBeVisible();
    });

    await test.step('run now streams into the routine session column', async () => {
      await page.getByRole('button', { name: 'Run now' }).click();
      const main = page.getByRole('main');
      await expect(main.getByRole('button', { name: 'Session menu', exact: true })).toBeVisible({ timeout: 30_000 });
      await expect(main.getByText('Routine', { exact: true })).toBeVisible();
      await expect(main.getByText('Weekly on Tuesday at 10:30').first()).toBeVisible();
    });

    await test.step('the run is recorded and its session reopens', async () => {
      await openRoutines(page);
      await page
        .getByRole('main')
        .getByRole('button', { name: /E2E edited review/ })
        .first()
        .click();
      const runs = page.getByLabel('E2E edited review recent runs');
      await expect(runs.getByText(/^(Running|Completed|Failed|Needs approval)$/).first()).toBeVisible({
        timeout: 30_000,
      });
      await runs.getByRole('button', { name: 'Open session' }).first().click();
      await expect(page.getByRole('main').getByText('Weekly on Tuesday at 10:30').first()).toBeVisible();
      await openRoutines(page);
    });

    await test.step('delete routine', async () => {
      await page
        .getByRole('main')
        .getByRole('button', { name: /E2E edited review/ })
        .first()
        .click();
      await page.getByRole('button', { name: 'More actions' }).click();
      await page.getByRole('menuitem', { name: 'Delete routine…' }).click();
      await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
      await expect(page.getByText('No routines yet')).toBeVisible();
    });
  });
});
