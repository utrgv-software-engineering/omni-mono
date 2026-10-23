import { unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';

import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, expectRunFinished, sessionPane } from 'tests/e2e/support/app';

test.use({ seedState: 'lazy-host-first-message' });

test('JavaScript filters and retrieves an automatically retained nested result', async ({ app }, testInfo) => {
  test.setTimeout(360_000);
  await writeFile(
    path.join(app.workspaceDir, 'oversized-output.txt'),
    `${'A'.repeat(90_000)}RETAINED_MIDDLE_NEEDLE${'B'.repeat(90_000)}`
  );
  await writeFile(path.join(app.workspaceDir, 'subagent-output.txt'), 'SUBAGENT_RETAINED_TOOL_OUTPUT');
  const page = app.page;
  const prompt = page.getByPlaceholder('How can I help you today?');
  await expect(prompt).toBeVisible({ timeout: 90_000 });
  await prompt.fill(`JAVASCRIPT_OUTPUT_PROOF ${path.join(app.workspaceDir, 'oversized-output.txt')}`);
  await prompt.press('Enter');
  await expect(page.getByText('JAVASCRIPT_FILTER_READY', { exact: true }).last()).toBeVisible({ timeout: 240_000 });
  await expectRunFinished(page);
  // Remove the source and restart the entire app: only the durable result can
  // satisfy the second request, and the model does not supply its result ID.
  await unlink(path.join(app.workspaceDir, 'oversized-output.txt'));
  const restored = await app.restart();
  await restored
    .getByRole('list', { name: 'Recents', exact: true })
    .getByRole('button', { name: /^JAVASCRIPT_OUTPUT_PROOF/ })
    .first()
    .click();
  await expect(restored.getByText('JAVASCRIPT_FILTER_READY', { exact: true }).last()).toBeVisible({ timeout: 90_000 });
  const conversation = sessionPane(restored, await activeSessionId(restored));
  const nextPrompt = conversation.getByRole('textbox', { name: 'How can I help you today?' });
  await expect(nextPrompt).toBeEditable({ timeout: 90_000 });
  await nextPrompt.fill('JAVASCRIPT_RELOAD_PROOF');
  await nextPrompt.press('Enter');
  await expect(conversation.getByText('JAVASCRIPT_FILTER_READY', { exact: true })).toHaveCount(2, { timeout: 240_000 });
  await nextPrompt.fill('JAVASCRIPT_SUBAGENT_PROOF');
  await nextPrompt.press('Enter');
  await expect(conversation.getByText('JAVASCRIPT_FILTER_READY', { exact: true })).toHaveCount(3, { timeout: 120_000 });
  const afterSubagent = await app.restart();
  await afterSubagent
    .getByRole('list', { name: 'Recents', exact: true })
    .getByRole('button', { name: /^JAVASCRIPT_OUTPUT_PROOF/ })
    .first()
    .click();
  await expect(afterSubagent.getByText('JAVASCRIPT_FILTER_READY', { exact: true }).last()).toBeVisible({
    timeout: 90_000,
  });
  const restoredConversation = sessionPane(afterSubagent, await activeSessionId(afterSubagent));
  const lastPrompt = restoredConversation.getByRole('textbox', { name: 'How can I help you today?' });
  await lastPrompt.fill('JAVASCRIPT_SUBAGENT_RELOAD');
  await lastPrompt.press('Enter');
  await expect(restoredConversation.getByText('JAVASCRIPT_FILTER_READY', { exact: true })).toHaveCount(4, {
    timeout: 120_000,
  });
  await testInfo.attach('javascript-filtered-result', {
    body: await afterSubagent.screenshot({ fullPage: true }),
    contentType: 'image/png',
  });
});
