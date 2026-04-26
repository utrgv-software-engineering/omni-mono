import { test, expect } from '@playwright/test';

test.describe('Navigation', () => {
  test('can navigate between all pages via sidebar links', async ({ page }) => {
    await page.goto('/');
    // Dashboard heading appears after API data loads
    await expect(page.locator('h1', { hasText: 'Dashboard' })).toBeVisible({ timeout: 15_000 });

    await page.getByRole('link', { name: 'Workouts' }).first().click();
    await expect(page.locator('h1', { hasText: 'Workouts' })).toBeVisible({ timeout: 15_000 });

    await page.getByRole('link', { name: 'Exercises' }).first().click();
    await expect(page.locator('h1', { hasText: 'Exercise Library' })).toBeVisible({ timeout: 15_000 });

    await page.getByRole('link', { name: 'Dashboard' }).first().click();
    await expect(page.locator('h1', { hasText: 'Dashboard' })).toBeVisible({ timeout: 15_000 });
  });
});
