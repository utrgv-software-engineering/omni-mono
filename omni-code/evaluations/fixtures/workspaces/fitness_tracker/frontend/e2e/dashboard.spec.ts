import { test, expect } from '@playwright/test';

const API = 'http://localhost:8000/api/v1';

test.describe('Dashboard', () => {
  test('displays stats and recent workouts table', async ({ page, request }) => {
    await request.post(`${API}/exercises/`, {
      data: { name: 'Deadlift', muscle_group: 'back' },
    });
    await request.post(`${API}/workouts/`, {
      data: { title: 'Pull Day', workout_date: '2026-03-01' },
    });

    await page.goto('/');
    const main = page.getByRole('main');
    await expect(page.locator('h1', { hasText: 'Dashboard' })).toBeVisible({ timeout: 15_000 });

    await expect(main.getByText('Total Workouts')).toBeVisible();
    await expect(main.getByText('Pull Day')).toBeVisible();
  });

  test('clicking a workout title navigates to detail page', async ({ page, request }) => {
    await request.post(`${API}/workouts/`, {
      data: { title: 'Leg Day', workout_date: '2026-03-02' },
    });

    await page.goto('/');
    await expect(page.locator('h1', { hasText: 'Dashboard' })).toBeVisible({ timeout: 15_000 });
    await page.getByRole('link', { name: 'Leg Day' }).click();
    await expect(page.locator('h1', { hasText: 'Leg Day' })).toBeVisible({ timeout: 15_000 });
  });
});
