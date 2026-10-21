import { test, expect } from '@playwright/test';

const API = 'http://localhost:8000/api/v1';

test.describe('Workouts and Entries CRUD', () => {
  test.beforeEach(async ({ request }) => {
    await request.post(`${API}/exercises/`, {
      data: { name: 'Bench Press', muscle_group: 'chest', description: 'Flat bench' },
    });
  });

  test('full workout lifecycle: create, view detail, add entry, edit entry, delete entry, delete workout', async ({
    page,
  }) => {
    // --- Create a workout ---
    await page.goto('/workouts');
    await expect(page.locator('h1', { hasText: 'Workouts' })).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: 'New Workout' }).first().click();
    await expect(page.locator('h2', { hasText: 'New Workout' })).toBeVisible();

    await page.getByPlaceholder('e.g. Upper Body Day').fill('Chest Day');
    await page.getByRole('button', { name: 'Create Workout' }).click();

    await expect(page.getByText('Chest Day')).toBeVisible({ timeout: 10_000 });

    // --- Navigate to workout detail ---
    await page.getByRole('link', { name: 'Chest Day' }).click();
    await expect(page.locator('h1', { hasText: 'Chest Day' })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('No exercises logged')).toBeVisible();

    // --- Add an exercise entry ---
    await page.getByRole('button', { name: 'Add Exercise' }).first().click();
    await expect(page.locator('h2', { hasText: 'Add Exercise' })).toBeVisible();
    await expect(page.locator('select')).toBeVisible();

    const form = page.locator('form');
    const setsInput = form.locator('input[type="number"]').first();
    const repsInput = form.locator('input[type="number"]').nth(1);
    const weightInput = form.locator('input[type="number"]').nth(2);

    await setsInput.clear();
    await setsInput.fill('4');
    await repsInput.clear();
    await repsInput.fill('8');
    await weightInput.fill('185');

    await form.getByRole('button', { name: 'Add Exercise' }).click();

    await expect(page.getByText('4 sets')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('8 reps')).toBeVisible();
    await expect(page.getByText('185 lbs')).toBeVisible();

    // --- Edit the entry ---
    const entryCard = page.locator('[class*="card-hover"]').first();
    await entryCard.locator('button').first().click();
    await expect(page.locator('h2', { hasText: 'Edit Exercise Entry' })).toBeVisible();

    const editForm = page.locator('form');
    const editReps = editForm.locator('input[type="number"]').nth(1);
    await editReps.clear();
    await editReps.fill('10');
    await page.getByRole('button', { name: 'Save Changes' }).click();

    await expect(page.getByText('10 reps')).toBeVisible({ timeout: 10_000 });

    // --- Delete the entry ---
    const entryCardAfterEdit = page.locator('[class*="card-hover"]').first();
    await entryCardAfterEdit.locator('button').nth(1).click();
    await expect(page.getByText('from this workout?')).toBeVisible();
    const entryConfirmModal = page.locator('.fixed.inset-0.z-50');
    await entryConfirmModal.getByRole('button', { name: 'Delete' }).click();
    await expect(page.getByText('No exercises logged')).toBeVisible({ timeout: 10_000 });

    // --- Delete the workout ---
    await page.locator('button', { hasText: 'Delete' }).first().click();
    await expect(page.getByText('Are you sure you want to delete')).toBeVisible();
    const workoutConfirmModal = page.locator('.fixed.inset-0.z-50');
    await workoutConfirmModal.getByRole('button', { name: 'Delete' }).click();

    await expect(page.locator('h1', { hasText: 'Workouts' })).toBeVisible({ timeout: 15_000 });
  });
});
