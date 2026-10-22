import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, newDeckChat, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

test('persisted response appears once in its own tile without its stream message', async ({ app }, info) => {
  test.setTimeout(240_000);
  const page = app.page;
  let suppressed = 0;
  let providerUpdates = 0;
  await page.routeWebSocket('**/*', (socket) => {
    const server = socket.connectToServer();
    server.onMessage((data) => {
      let frame;
      try {
        frame = JSON.parse(String(data));
      } catch {}
      if (frame?.method === 'item_updated' && frame.params?.kind === 'agent_message') {
        providerUpdates++;
      }
      if (frame?.method === 'message_output' && frame.params?.content === 'SESSION_A_REPLY') {
        suppressed++;
        // Omit only the transcript notification, keeping the sequence slot
        // as a token so transport gap recovery cannot mask this regression.
        // The backend unit test separately proves real SDK event loss.
        socket.send(JSON.stringify({ ...frame, method: 'token', params: { ...frame.params, content: '' } }));
      } else {
        socket.send(data);
      }
    });
  });
  await page.reload();
  const composer = visibleComposer(page);
  await expect(composer).toBeVisible({ timeout: 90_000 });
  const ids: string[] = [];
  for (let index = 0; index < 2; index++) {
    if (index) {
      await newDeckChat(page);
      await expect.poll(() => activeSessionId(page)).not.toBe(ids[0]);
    }
    ids.push(await activeSessionId(page));
    const column = sessionPane(page, ids[index]!);
    await composer.fill(`PERSISTENCE_SETUP_${index}`);
    await composer.press('Enter');
    await expect(column.getByText('HOST_FIRST_MESSAGE_READY', { exact: true })).toBeVisible({ timeout: 90_000 });
    await expect(column.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0, { timeout: 90_000 });
    await expect(column.getByText('HOST_FIRST_MESSAGE_READY', { exact: true })).toHaveCount(1);
  }
  const a = sessionPane(page, ids[0]!);
  const b = sessionPane(page, ids[1]!);
  await composer.fill('independent persistence-check draft');
  const updatesBefore = providerUpdates;
  await openSession(page, 'PERSISTENCE_SETUP_0');
  await composer.fill('SOAK_PERSISTENCE WAIT_FOR_SESSION_A');
  await composer.press('Enter');
  await expect(a.getByText('SESSION_A_REPLY', { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(a.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0, { timeout: 90_000 });
  expect(suppressed).toBeGreaterThan(0);
  expect(providerUpdates).toBeGreaterThan(updatesBefore);
  await expect(a.getByText('SESSION_A_REPLY', { exact: true })).toHaveCount(1);
  await expect(b.getByText('SESSION_A_REPLY', { exact: true })).toHaveCount(0);
  await attachProofPng(info, 'persisted-response-owning-session', await app.captureScreenshot());
  await openSession(page, 'PERSISTENCE_SETUP_1');
  await expect(composer).toHaveValue('independent persistence-check draft');
  await page.reload();
  await openSession(page, 'PERSISTENCE_SETUP_0');
  await expect(a.getByText('SESSION_A_REPLY', { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(a.getByText('SESSION_A_REPLY', { exact: true })).toHaveCount(1);
  await expect(b.getByText('SESSION_A_REPLY', { exact: true })).toHaveCount(0);
  await openSession(page, 'PERSISTENCE_SETUP_1');
  // Drafts live in memory: a reload starts every composer empty.
  await expect(composer).toHaveValue('');
});
