import type { Page } from '@playwright/test';
import { expect, test } from 'tests/e2e/fixtures/test';
import { newDeckChat } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

async function holdCommand(page: Page, method: string) {
  await page.evaluate((method) => {
    const send = WebSocket.prototype.send;
    WebSocket.prototype.send = function (data) {
      let frame;
      try {
        frame = JSON.parse(String(data));
      } catch {}
      if (frame?.channel === 'store:chat-command' && frame.args?.[0]?.method === method) {
        WebSocket.prototype.send = send;
        (window as unknown as { releaseChatCommand: () => void }).releaseChatCommand = () => send.call(this, data);
        return;
      }
      send.call(this, data);
    };
  }, method);
}
async function releaseCommand(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => typeof (window as unknown as { releaseChatCommand?: () => void }).releaseChatCommand)
    )
    .toBe('function');
  await page.evaluate(() => {
    const target = window as unknown as { releaseChatCommand?: () => void };
    target.releaseChatCommand!();
    delete target.releaseChatCommand;
  });
}
test('concurrent windows retain both new chats', async ({ app, mode }, testInfo) => {
  test.skip(mode !== 'server-local', 'Two browser windows share the launcher authority; Electron has a single window.');
  const a = app.page;
  await expect(a.getByRole('textbox', { name: 'How can I help you today?' })).toBeVisible({ timeout: 90_000 });
  const b = await a.context().newPage();
  try {
    await b.goto(a.url());
    await expect(b.getByRole('textbox', { name: 'How can I help you today?' })).toBeVisible({ timeout: 90_000 });
    const columnsA = a.locator('[data-deck-column]');
    const columnsB = b.locator('[data-deck-column]');
    await expect(columnsA).toHaveCount(1);
    await expect(columnsB).toHaveCount(1);
    await holdCommand(b, 'addTab');
    await newDeckChat(b);
    await newDeckChat(a);
    await expect(columnsA).toHaveCount(2);
    await releaseCommand(b);
    await expect(columnsA).toHaveCount(3);
    await expect(columnsB).toHaveCount(3);
    const ids = await columnsA.evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('data-deck-column'))
    );
    expect(new Set(ids).size).toBe(3);
    await expect
      .poll(() =>
        columnsB.evaluateAll((elements) => elements.map((element) => element.getAttribute('data-deck-column')))
      )
      .toEqual(ids);
    await attachProofPng(testInfo, 'both concurrent chat additions survive', await a.screenshot());
  } finally {
    await b.close();
  }
});
