import { expect, test } from 'tests/e2e/fixtures/test';
import { activeSessionId, newDeckChat, openSession, sessionPane, visibleComposer } from 'tests/e2e/support/app';
import { attachProofPng } from 'tests/e2e/support/proof';

test.use({ seedState: 'lazy-host-first-message' });

for (const crash of [false, true]) {
  test(`retires orphaned approvals and questions after a ${crash ? 'crash' : 'graceful'} server restart`, async ({
    app,
    mode,
  }, testInfo) => {
    test.skip(mode !== 'server-local', 'This audit restarts the launcher server and its child agent runtime');
    test.setTimeout(240_000);
    let page = app.page;
    const composer = visibleComposer(page);
    await expect(composer).toBeVisible({ timeout: 90_000 });
    const aId = await activeSessionId(page);
    await composer.fill('TILE_APPROVAL_A');
    await composer.press('Enter');
    await expect(page.getByRole('button', { name: 'Approve Once', exact: true })).toBeVisible({ timeout: 90_000 });
    await newDeckChat(page);
    await expect.poll(() => activeSessionId(page)).not.toBe(aId);
    const bId = await activeSessionId(page);
    await composer.fill('TILE_QUESTION_B');
    await composer.press('Enter');
    await expect(sessionPane(page, bId).getByText('Question for tile B', { exact: true })).toBeVisible({
      timeout: 90_000,
    });
    await attachProofPng(testInfo, 'pending before full server restart', await app.captureScreenshot());

    // Stops the isolated launcher process group (including its child agent),
    // then starts fresh processes using the same isolated on-disk profile.
    page = await app.restart({ crash });
    const restoredA = sessionPane(page, aId);
    const restoredB = sessionPane(page, bId);
    const restoredComposer = visibleComposer(page);
    await openSession(page, 'TILE_QUESTION_B');
    await expect(restoredComposer).toBeEditable({ timeout: 90_000 });
    await expect(restoredB.getByRole('log')).toContainText('TILE_QUESTION_B');
    // A dead server-side waiter leaves nothing running.
    await expect(restoredB.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);
    await openSession(page, 'TILE_APPROVAL_A');
    await expect(restoredComposer).toBeEditable({ timeout: 90_000 });
    await expect(restoredA.getByRole('log')).toContainText('TILE_APPROVAL_A');
    await attachProofPng(testInfo, 'recovered after full server restart', await app.captureScreenshot());
    // A dead server-side waiter cannot support an actionable approval card.
    const approve = restoredA.getByRole('button', { name: 'Approve Once', exact: true });
    await expect.soft(approve).toHaveCount(0);
    if (await approve.isVisible()) {
      await approve.click();
      // The decision cannot land; the card stays actionable and says nothing.
      await expect(approve).toBeEnabled();
      await expect(restoredA.getByRole('alert')).toHaveCount(0);
      await attachProofPng(testInfo, 'orphan approval cannot be resolved', await app.captureScreenshot());
    }
  });
}
