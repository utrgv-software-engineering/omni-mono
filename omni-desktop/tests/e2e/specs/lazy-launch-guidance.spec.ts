import type { Page, TestInfo } from '@playwright/test';
import { expect, test } from 'tests/e2e/fixtures/test';
import { invokeApp } from 'tests/e2e/support/invoke';

import { CHAT_SUGGESTIONS } from '@/renderer/features/Code/empty-suggestions';

const tourPrompt = CHAT_SUGGESTIONS.find((suggestion) => suggestion.label === 'Show me around')!.prompt;

async function captureState(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  const screenshot = await page.screenshot({ fullPage: true });
  await testInfo.attach(name, { body: screenshot, contentType: 'image/png' });
}

async function waitForChat(page: Page): Promise<void> {
  await expect(page.getByPlaceholder('How can I help you today?').first()).toBeVisible({ timeout: 90_000 });
}

test.describe('lazy session launch guidance', () => {
  test.describe('without a workspace', () => {
    test.use({ seedState: 'no-workspace' });

    test('explains the prerequisite inline and links to workspace settings', async ({ app }, testInfo) => {
      await waitForChat(app.page);
      await invokeApp(app.page, 'store:set-key', 'workspaceDir', '');
      await expect(
        app.page.getByRole('heading', { name: 'Choose a workspace folder to start chatting' })
      ).toBeVisible();
      await expect(app.page.getByPlaceholder('How can I help you today?')).toBeDisabled();
      await captureState(app.page, testInfo, '01-workspace-required');

      await app.page.getByRole('button', { name: 'Open workspace settings' }).click();
      await expect(app.page.getByText('Workspace directory', { exact: true })).toBeVisible();
      await captureState(app.page, testInfo, '02-workspace-settings');
    });
  });

  test.describe('with a workspace', () => {
    test.use({ seedState: 'lazy-ready' });

    test('shows first-message guidance and starts from a suggested prompt', async ({ app }, testInfo) => {
      await waitForChat(app.page);
      await expect(app.page.getByText(/Your first message starts a session in/)).toBeVisible();
      await expect(app.page.getByRole('button', { name: 'Plan my week' })).toBeVisible();
      await captureState(app.page, testInfo, '03-ready-to-start');

      await app.page.getByRole('button', { name: 'Show me around' }).click();
      await expect(app.page.getByText(tourPrompt)).toBeVisible();
      await expect(app.page.getByRole('status').filter({ hasText: 'Starting' })).toBeVisible();
      await captureState(app.page, testInfo, '04-starting-with-pending-intent');
    });
  });

  test.describe('on the first submitted message', () => {
    test.use({ seedState: 'lazy-host-first-message' });

    test('starts the agent and returns its response', async ({ app }, testInfo) => {
      test.setTimeout(360_000);
      if (process.env.E2E_FORWARD_ELECTRON_LOGS === '1') {
        app.page.on('console', (message) => console.log(`[renderer:${message.type()}] ${message.text()}`));
      }
      await waitForChat(app.page);

      const prompt = 'Respond with exactly HOST_FIRST_MESSAGE_READY and nothing else.';
      const composer = app.page.getByPlaceholder('How can I help you today?');
      await composer.fill(prompt);
      await composer.press('Enter');
      await expect(app.page.getByText(prompt, { exact: true }).last()).toBeVisible();

      await expect
        .poll(
          async () => {
            const current = await invokeApp<{ type?: string }>(
              app.page,
              'agent-process:get-status',
              'chat-e2e-host-first-message'
            );
            return current?.type;
          },
          { timeout: 300_000 }
        )
        .toBe('running');
      await expect(app.page.getByRole('alert').filter({ hasText: 'Couldn’t start' })).toHaveCount(0);
      await expect(app.page.getByText('HOST_FIRST_MESSAGE_READY', { exact: true }).last()).toBeVisible({
        timeout: 180_000,
      });
      await expect(app.page.getByTestId('chat-transcript').getByText(prompt, { exact: true })).toHaveCount(1);
      await captureState(app.page, testInfo, 'host-first-message-agent-response');
    });
  });
});
