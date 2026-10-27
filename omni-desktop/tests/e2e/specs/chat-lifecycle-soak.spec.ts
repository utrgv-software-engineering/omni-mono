import { readFileSync } from 'node:fs';

import type { Locator, Page } from '@playwright/test';
import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, expectRunFinished, openChat, openSession, sessionPane } from 'tests/e2e/support/app';
import { invokeApp } from 'tests/e2e/support/invoke';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({
  seedState: 'lazy-host-first-message',
  // Keep actions/network diagnostics, explicit owner screenshots and the
  // full proof video. Per-action DOM snapshots over thirty minutes produce
  // multi-gigabyte traces; the focused story specs retain those snapshots.
  trace: { mode: 'on', snapshots: false, screenshots: false, sources: true },
});

const duration = Number(process.env.OMNI_CHAT_SOAK_MS ?? 0);
const textbox = (owner: Page | Locator) => owner.getByRole('textbox', { name: 'How can I help you today?' });
const dead = (pid: number) => {
  try {
    return readFileSync(`/proc/${pid}/stat`, 'utf8').split(') ')[1]?.startsWith('Z ') === true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return true;
    }
    throw error;
  }
};

test('sustains mixed conversation lifecycles and checks ownership and retirement', async ({ app, mode }, testInfo) => {
  test.skip(!duration, 'Opt in with OMNI_CHAT_SOAK_MS (1800000 for thirty minutes)');
  test.skip(mode !== 'server-local', 'Backend-only crash and /proc resource assertions require Linux server mode');
  test.setTimeout(duration + 360_000);
  const started = Date.now();
  const samples: object[] = [];
  let cycle = 0;
  let idleSocketBaseline: number | undefined;
  let page = app.page;
  const instrument = () => {
    const sockets = new Set<WebSocket>();
    const operations = new WeakMap<WebSocket, { first: string; last: string }>();
    const send = WebSocket.prototype.send;
    (window as any).soakDisconnect = () => sockets.forEach((socket) => socket.close(4001, 'soak reconnect'));
    (window as any).soakSocketCount = () =>
      [...sockets].filter((socket) => socket.readyState < WebSocket.CLOSING).length;
    (window as any).soakSocketPaths = () =>
      [...sockets]
        .filter((socket) => socket.readyState < WebSocket.CLOSING)
        .map((socket) => new URL(socket.url).pathname.replace(/\/proxy\/[^/]+/, '/proxy/:runtime'));
    (window as any).soakSocketDetails = () =>
      [...sockets]
        .filter((socket) => socket.readyState < WebSocket.CLOSING)
        .map((socket) => ({
          path: new URL(socket.url).pathname.replace(/\/proxy\/[^/]+/, '/proxy/:runtime'),
          state: socket.readyState,
          ...operations.get(socket),
        }));
    WebSocket.prototype.send = function (data) {
      try {
        const frame = JSON.parse(String(data));
        const operation = String(frame.method ?? frame.channel ?? 'response');
        operations.set(this, { first: operations.get(this)?.first ?? operation, last: operation });
      } catch {
        /* Only record method names, never credentials or payloads. */
      }
      if (!sockets.has(this)) {
        sockets.add(this);
        this.addEventListener('close', () => sockets.delete(this), { once: true });
        const receive = this.onmessage;
        this.onmessage = function (event) {
          receive?.call(this, event);
          let frame;
          try {
            frame = JSON.parse(String(event.data));
          } catch {
            return;
          }
          if (frame.method && typeof frame.params?.seq === 'number') {
            setTimeout(() => receive?.call(this, event), 15);
          }
        };
      }
      send.call(this, data);
    };
  };
  await page.addInitScript(instrument);
  await page.reload();

  const archive = async (column: Locator) => {
    const id = await column.getAttribute('data-deck-column');
    if (id !== (await activeSessionId(page))) {
      await invokeApp(page, 'store:chat-command', { method: 'setActiveTab', args: [id] });
    }
    await column.getByRole('button', { name: 'Session menu', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Archive session', exact: true }).click();
    await expect(column).toHaveCount(0, { timeout: 90_000 });
  };

  try {
    do {
      cycle++;
      const prefix = `SOAK_${String(cycle).padStart(4, '0')}`;
      console.log('CHAT_SOAK_PHASE', cycle, 'create mixed sessions');
      const prompts = [
        `${prefix}_A TILE_UI_A WAIT_FOR_SESSION_A`,
        `${prefix}_B TILE_QUESTION_B`,
        `${prefix}_C TILE_APPROVAL_C`,
      ];
      const ids: string[] = [];
      const show = (owner: number) => openSession(page, prompts[owner]!);
      for (let owner = 0; owner < 3; owner++) {
        // Sidebar "New chat" reuses a pristine chat, so no empty chats pile up.
        await openChat(page);
        const column = sessionPane(page, await activeSessionId(page));
        await expect(textbox(column)).toBeEditable({ timeout: 90_000 });
        ids.push((await column.getAttribute('data-deck-column'))!);
        await textbox(column).fill(prompts[owner]!);
        await textbox(column).press('Enter');
      }
      const [a, b, c] = ids.map((id) => sessionPane(page, id)) as [Locator, Locator, Locator];
      await show(0);
      await expect(a.getByText('A_ARTIFACT_initial', { exact: true })).toBeVisible({ timeout: 120_000 });
      await show(1);
      await expect(b.getByText('Question for tile B', { exact: true })).toBeVisible({ timeout: 90_000 });
      await show(2);
      await expect(c.getByRole('button', { name: 'Approve Once', exact: true })).toBeVisible({ timeout: 90_000 });
      await show(1);
      await textbox(b).fill(`${prefix}_B_DRAFT`);
      await b
        .locator('input[type="file"]')
        .setInputFiles({ name: `${prefix}_B.txt`, mimeType: 'text/plain', buffer: Buffer.from(prefix) });
      await page.evaluate(() => (window as any).soakDisconnect());
      await show(0);
      await expect(textbox(a)).toBeEditable({ timeout: 90_000 });
      await expectRunFinished(a);
      await a.getByRole('button', { name: /GPT 5\.2 E2E/ }).click();
      await page.getByRole('menuitemradio', { name: /GPT 5\.2 Mini E2E/ }).click();
      await expect(a.getByRole('button', { name: /GPT 5\.2 Mini E2E/ })).toBeVisible();
      await show(1);
      await expect(b.getByRole('button', { name: /GPT 5\.2 E2E/ })).toBeVisible();
      await show(0);
      const accepted = `${prefix}_ACCEPTED`;
      await textbox(a).fill(accepted);
      await textbox(a).press('Enter');
      await expect(a.getByRole('log').getByText(accepted, { exact: true })).toHaveCount(1);
      await expect(a.getByRole('log').getByText('SESSION_A_REPLY', { exact: true })).toHaveCount(2, {
        timeout: 90_000,
      });
      expect(app.inspectModelRequests().filter((request) => request.marker === accepted)).toEqual([
        { marker: accepted, model: 'gpt-5.2-mini' },
      ]);
      if (cycle % Number(process.env.OMNI_CHAT_SOAK_RELOAD_EVERY ?? 7) === 0) {
        await page.reload();
      }
      await show(1);
      await expect(textbox(b)).toHaveValue(`${prefix}_B_DRAFT`, { timeout: 90_000 });
      await expect(b.getByText(`${prefix}_B.txt`, { exact: true }).first()).toBeVisible();
      await show(0);
      await expect(a.getByRole('log').getByText(accepted, { exact: true })).toHaveCount(1);
      await expect(a.getByText('A_ARTIFACT_initial', { exact: true })).toHaveCount(1);
      await expect(a.getByText('A_PLAN_initial', { exact: true }).first()).toBeVisible();
      for (let owner = 0; owner < 3; owner++) {
        const column = [a, b, c][owner]!;
        await show(owner);
        for (let other = 0; other < 3; other++) {
          if (other !== owner) {
            await expect(column.getByRole('log')).not.toContainText(prompts[other]!);
          }
        }
      }
      const original = app.storedTabs().find((tab) => tab.id === ids[2])!;
      console.log('CHAT_SOAK_PHASE', cycle, 'archive and restore approval owner');
      await archive(c);
      await page.getByRole('button', { name: 'Archived sessions', exact: true }).click();
      // Only this cycle's C is newly archived; select its row by visible title.
      const dialog = page.getByRole('dialog');
      const row = dialog
        .locator('div')
        .filter({ has: page.getByText(prompts[2]!, { exact: true }) })
        .filter({ has: page.getByRole('button', { name: 'Restore', exact: true }) })
        .last();
      await row.getByRole('button', { name: 'Restore', exact: true }).click();
      await page.keyboard.press('Escape');
      await show(2);
      await expect
        .poll(() => app.storedTabs().find((tab) => tab.sessionId === original.sessionId)?.id)
        .not.toBeUndefined();
      const restored = app.storedTabs().find((tab) => tab.sessionId === original.sessionId)!;
      const restoredC = sessionPane(page, restored.id);
      await expect(restoredC.getByRole('log')).toContainText(prompts[2]!);
      await expect(restoredC.getByRole('button', { name: 'Approve Once', exact: true })).toHaveCount(0);
      await textbox(restoredC).fill(`${prefix}_RESTORED`);
      await textbox(restoredC).press('Enter');
      await expect(restoredC.getByRole('log')).toContainText('HOST_FIRST_MESSAGE_READY', { timeout: 90_000 });
      await expectRunFinished(restoredC);
      await show(1);
      await expect(b.getByText('Question for tile B', { exact: true })).toBeVisible();
      const { workerPids, ...resources } = app.inspectRuntimeResources();
      if (cycle === 1 || cycle % 5 === 0) {
        for (let owner = 0; owner < 3; owner++) {
          await show(owner);
          await attachProofPng(
            testInfo,
            `${prefix} owner ${owner} isolated conversation`,
            await app.captureScreenshot()
          );
        }
      }
      const hosts = [...app.runtimePids(), ...workerPids];
      const cId = restored.id;
      if (cycle % Number(process.env.OMNI_CHAT_SOAK_RESTART_EVERY ?? 10) === 0) {
        console.log('CHAT_SOAK_PHASE', cycle, 'backend-only crash');
        const previousPage = page;
        page = await app.restart({ crash: true, backendOnly: true });
        expect(page).toBe(previousPage);
        await expect.poll(() => hosts.every(dead), { timeout: 90_000 }).toBe(true);
        await show(1);
        await expect(textbox(sessionPane(page, ids[1]!))).toHaveValue(`${prefix}_B_DRAFT`, {
          timeout: 90_000,
        });
        await expect(sessionPane(page, ids[1]!).getByText(`${prefix}_B.txt`, { exact: true }).first()).toBeVisible();
        await show(2);
        const resumedC = sessionPane(page, cId);
        await expect(textbox(resumedC)).toBeEditable({ timeout: 90_000 });
        await textbox(resumedC).fill(`${prefix}_AFTER_CRASH`);
        await textbox(resumedC).press('Enter');
        await expect(resumedC.getByRole('log').getByText('HOST_FIRST_MESSAGE_READY', { exact: true })).toHaveCount(2, {
          timeout: 90_000,
        });
      }
      console.log('CHAT_SOAK_PHASE', cycle, 'retire all owners');
      for (const id of [ids[0]!, ids[1]!, cId]) {
        await archive(sessionPane(page, id));
      }
      // Backend restart/reload can introduce an extra empty draft chat.
      // Retire those through the same user action so the harness itself does
      // not accumulate deliberately open, unused chats between cycles.
      const extras = app.storedTabs().slice(1);
      for (const tab of extras) {
        await archive(sessionPane(page, tab.id));
      }
      console.log('CHAT_SOAK_PHASE', cycle, 'verify retirement');
      // The targetless product-management consumer intentionally shares a
      // host with chats, so a host may stay alive, but retired chats
      // must not leave per-session workers behind.
      await expect.poll(() => app.inspectRuntimeResources().childProcesses, { timeout: 30_000 }).toBe(0);
      const idle = app.inspectRuntimeResources();
      expect(idle.hosts).toBeLessThanOrEqual(2);
      if (idleSocketBaseline !== undefined) {
        await expect
          .poll(() => page.evaluate(() => (window as any).soakSocketCount()), { timeout: 30_000 })
          .toBeLessThanOrEqual(idleSocketBaseline + 2);
      }
      const browserState = await page.evaluate(() => ({
        nodes: document.querySelectorAll('*').length,
        sockets: (window as any).soakSocketCount?.(),
        heapBytes: (performance as any).memory?.usedJSHeapSize,
        socketPaths: (window as any).soakSocketPaths?.(),
      }));
      idleSocketBaseline ??= browserState.sockets;
      const sample = {
        cycle,
        elapsedMs: Date.now() - started,
        ...resources,
        ...browserState,
        liveSharedHosts: idle.hosts,
        idleRssBytes: idle.rssBytes,
        idleFileDescriptors: idle.fileDescriptors,
        idleChildProcesses: idle.childProcesses,
      };
      samples.push(sample);
      console.log('CHAT_SOAK_CYCLE', JSON.stringify(sample));
      if (cycle === 1 || cycle % 5 === 0) {
        await attachProofPng(testInfo, `cycle ${cycle} retired resources`, await app.captureScreenshot());
      }
    } while (Date.now() - started < duration || cycle < Number(process.env.OMNI_CHAT_SOAK_MIN_CYCLES ?? 1));
  } finally {
    const sockets = await page.evaluate(() => (window as any).soakSocketDetails?.()).catch(() => null);
    await testInfo.attach('lifecycle soak samples', {
      body: JSON.stringify(
        { requestedMs: duration, elapsedMs: Date.now() - started, cycles: cycle, samples, sockets },
        null,
        2
      ),
      contentType: 'application/json',
    });
  }
});
