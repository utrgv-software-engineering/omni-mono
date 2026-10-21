import { test, expect } from '@playwright/test';

test.describe('Exercises CRUD', () => {
  test('create, filter, edit, and delete an exercise', async ({ page }) => {
    await page.goto('/exercises');
    await expect(page.locator('h1', { hasText: 'Exercise Library' })).toBeVisible({ timeout: 15_000 });

    // Create first exercise
    await page.getByRole('button', { name: 'New Exercise' }).first().click();
    await expect(page.locator('h2', { hasText: 'New Exercise' })).toBeVisible();

    await page.getByPlaceholder('e.g. Bench Press').fill('Barbell Row');
    await page.getByPlaceholder('e.g. chest, legs, back').fill('back');
    await page.getByPlaceholder('Optional description').fill('Bent over row');
    await page.getByRole('button', { name: 'Create Exercise' }).click();

    // Wait for modal to fully close, verify exercise appears
    await expect(page.getByRole('heading', { name: 'Barbell Row' })).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('h2', { hasText: 'New Exercise' })).not.toBeVisible();

    // Create second exercise in a different group
    await page.getByRole('button', { name: 'New Exercise' }).first().click();
    await expect(page.locator('h2', { hasText: 'New Exercise' })).toBeVisible();

    await page.getByPlaceholder('e.g. Bench Press').fill('Squat');
    await page.getByPlaceholder('e.g. chest, legs, back').fill('legs');
    await page.getByPlaceholder('Optional description').fill('Barbell squat');
    await page.getByRole('button', { name: 'Create Exercise' }).click();

    await expect(page.getByRole('heading', { name: 'Squat' })).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('h2', { hasText: 'New Exercise' })).not.toBeVisible();

    // Filter by muscle group
    await page.getByRole('button', { name: 'back', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Barbell Row' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Squat' })).not.toBeVisible();

    // Go back to all
    await page.getByRole('button', { name: 'All' }).click();
    await expect(page.getByRole('heading', { name: 'Squat' })).toBeVisible({ timeout: 10_000 });

    // Edit
    const rowCard = page.locator('[class*="card-hover"]', { hasText: 'Barbell Row' });
    await rowCard.hover();
    await rowCard.locator('button').first().click();
    await expect(page.locator('h2', { hasText: 'Edit Exercise' })).toBeVisible();

    await page.getByPlaceholder('Optional description').fill('Updated description');
    await page.getByRole('button', { name: 'Save Changes' }).click();
    await expect(page.getByText('Updated description')).toBeVisible({ timeout: 10_000 });

    // Delete
    const rowCardAfterEdit = page.locator('[class*="card-hover"]', { hasText: 'Barbell Row' });
    await rowCardAfterEdit.hover();
    await rowCardAfterEdit.locator('button').nth(1).click();
    await expect(page.getByText('Are you sure you want to delete')).toBeVisible();
    await page.getByRole('button', { name: 'Delete' }).click();
    await expect(page.getByRole('heading', { name: 'Barbell Row' })).not.toBeVisible({ timeout: 10_000 });
  });
});
