import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, newDeckChat, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

for (const scenario of ['stream', 'approval', 'artifact'] as const) {
  test(`background session owns ${scenario} through switching, resize and native minimize`, async ({
    app,
    mode,
  }, info) => {
    test.setTimeout(240_000);
    const page = app.page;
    await page.addInitScript(() => {
      const send = WebSocket.prototype.send;
      const tracked = new WeakSet<WebSocket>();
      (window as any).backgroundEvents = [];
      WebSocket.prototype.send = function (data) {
        if (!tracked.has(this)) {
          tracked.add(this);
          this.addEventListener('message', (event) => {
            let frame;
            try {
              frame = JSON.parse(String(event.data));
            } catch {
              return;
            }
            if (
              ['message_output', 'client_request', 'tool_approval_requested'].includes(frame.method) &&
              frame.params?.session_id
            ) {
              (window as any).backgroundEvents.push({
                method: frame.method,
                session: frame.params.session_id,
                text: JSON.stringify(frame.params),
              });
            }
          });
        }
        return send.call(this, data);
      };
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
      await composer.fill(`BACKGROUND_${index}`);
      await composer.press('Enter');
      await expect(sessionPane(page, ids[index]!).getByText('HOST_FIRST_MESSAGE_READY', { exact: true })).toBeVisible({
        timeout: 90_000,
      });
    }
    const a = sessionPane(page, ids[0]!);
    const b = sessionPane(page, ids[1]!);
    const prompt =
      scenario === 'stream'
        ? 'SOAK_BACKGROUND WAIT_FOR_SESSION_A'
        : scenario === 'approval'
          ? 'TILE_APPROVAL_A'
          : 'TILE_UI_A';
    const marker =
      scenario === 'stream' ? 'SESSION_A_REPLY' : scenario === 'approval' ? 'TILE_APPROVAL_A' : 'A_ARTIFACT_initial';
    await openSession(page, 'BACKGROUND_0');
    await expect(a.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0, { timeout: 90_000 });
    await expect(composer).toBeEditable({ timeout: 90_000 });
    await composer.fill(`${prompt} BACKGROUND_LIFECYCLE_GATE`);
    await a.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(composer).toHaveValue('');
    // A now works in the background while B is shown with its own draft.
    await openSession(page, 'BACKGROUND_1');
    await composer.fill('independent background-check draft');
    await app.resizeWindow(1100, 800);
    if (mode === 'electron-local') {
      await app.setMinimized(true);
    }
    try {
      app.releaseBackground();
      await expect
        .poll(
          () =>
            page.evaluate(
              (expected) =>
                (window as any).backgroundEvents.some((event: { text: string }) => event.text.includes(expected)),
              marker
            ),
          { timeout: 90_000 }
        )
        .toBe(true);
    } finally {
      if (mode === 'electron-local') {
        await app.setMinimized(false);
      }
    }
    await app.resizeWindow(1920, 1080);
    await expect(composer).toHaveValue('independent background-check draft');
    await expect(page.getByText(marker, { exact: true })).not.toBeVisible();
    await expect(b).not.toContainText(marker);
    if (scenario === 'approval') {
      await expect(page.getByRole('button', { name: 'Approve Once', exact: true })).toHaveCount(0);
    }
    await openSession(page, 'BACKGROUND_0');
    if (scenario === 'approval') {
      await expect(a.getByRole('button', { name: 'Approve Once', exact: true })).toBeVisible();
      await a.getByRole('button', { name: 'Approve Once', exact: true }).click();
      await expect(a.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0, { timeout: 90_000 });
    } else {
      await expect(a.getByText(marker, { exact: true })).toBeVisible({ timeout: 90_000 });
    }
    if (scenario === 'artifact') {
      await expect(a.getByText('A_PLAN_initial', { exact: true }).first()).toBeVisible();
      await a.getByRole('button', { name: 'Toggle artifacts', exact: true }).click();
      await a.getByRole('button', { name: 'A artifact', exact: true }).click();
      await expect(a.getByText(marker, { exact: true })).toBeVisible();
    }
    await attachProofPng(info, `background ${scenario} retains its session`, await app.captureScreenshot());
    await openSession(page, 'BACKGROUND_1');
    await expect(composer).toHaveValue('independent background-check draft');
  });
}
