import { expect, test } from 'tests/e2e/fixtures/test';
import { openSession, sessionPane, sessionRow, visibleComposer } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';
import { LARGE_HISTORY_SESSIONS } from 'tests/e2e/support/state';

test.use({ seedState: 'large-history' });

// Both chats are persisted before boot (see seedLargeHistory), so the app
// hydrates 751 saved turns per session the way a returning user's would.
test('large persisted histories stay isolated while scrolling, streaming and switching sessions', async ({
  app,
}, info) => {
  test.setTimeout(360_000);
  const page = app.page;
  const composer = visibleComposer(page);
  const columns = LARGE_HISTORY_SESSIONS.map(({ tabId }) => sessionPane(page, tabId));
  const hydrateStart = Date.now();
  // Titles arrive with the runtime's thread index, after the first-run install.
  await expect(sessionRow(page, LARGE_HISTORY_SESSIONS[1]!.title)).toBeVisible({ timeout: 120_000 });
  for (const [i, { title, prefix }] of LARGE_HISTORY_SESSIONS.entries()) {
    const other = LARGE_HISTORY_SESSIONS[1 - i]!.prefix;
    await openSession(page, title);
    await expect(columns[i]!.getByRole('log').getByText(`${prefix}_0749`, { exact: true })).toHaveCount(1, {
      timeout: 90_000,
    });
    await expect(columns[i]!.getByRole('log')).not.toContainText(`${other}_`);
    await expect(columns[i]!.getByRole('log').getByText(new RegExp(`^${prefix}_\\d{4}$`))).toHaveCount(750);
    await expect(composer).toBeEditable({ timeout: 90_000 });
  }
  const hydrateMs = Date.now() - hydrateStart;
  await openSession(page, 'LARGE_HISTORY_0');
  await composer.fill('SOAK_LARGE_HISTORY WAIT_FOR_SESSION_A');
  await columns[0]!.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(columns[0]!.getByRole('button', { name: 'Stop', exact: true })).toBeVisible();
  const logA = columns[0]!.getByRole('log');
  await logA.hover();
  // Offscreen messages hold an estimated height until first drawn, so one
  // fling can land short of the top while they settle; keep scrolling like a
  // reader would.
  await expect(async () => {
    await page.mouse.wheel(0, -200000);
    await expect(logA.getByText('HISTORY_0_0000', { exact: true })).toBeInViewport({ timeout: 1_000 });
  }).toPass({ timeout: 30_000 });
  await openSession(page, 'LARGE_HISTORY_1');
  await composer.fill('second session draft during long-history stream');
  for (let i = 0; i < 3; i++) {
    await openSession(page, 'LARGE_HISTORY_0');
    await openSession(page, 'LARGE_HISTORY_1');
    await expect(composer).toHaveValue('second session draft during long-history stream');
  }
  await expect(columns[0]!.getByText('SESSION_A_REPLY', { exact: true })).toHaveCount(1, { timeout: 90_000 });
  await expect(columns[1]!.getByRole('log')).not.toContainText('SESSION_A_REPLY');
  await openSession(page, 'LARGE_HISTORY_0');
  // Switching away and back keeps A's reading position.
  await expect(logA.getByText('HISTORY_0_0000', { exact: true })).toBeInViewport();
  await columns[0]!.getByRole('button', { name: 'Jump to latest message', exact: true }).click();
  await expect(logA.getByText('SESSION_A_REPLY', { exact: true })).toBeInViewport();
  await info.attach('history hydration timing', {
    body: JSON.stringify({ items: 1502, hydrateMs }),
    contentType: 'application/json',
  });
  await attachProofPng(
    info,
    'large histories retain session ownership through streaming and scroll',
    await page.screenshot()
  );
});
