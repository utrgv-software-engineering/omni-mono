import { afterEach, expect, it } from 'vitest';

import { ConversationSession } from './conversation-session';
import { fakeSessionClient } from './session-test-support';

const sessions: ConversationSession[] = [];
afterEach(() => sessions.splice(0).forEach((session) => session.dispose()));

function setup(functions: string[]) {
  const { fake, client } = fakeSessionClient();
  fake.listServerFunctions.mockResolvedValue(functions.map((name) => ({ name })));
  const session = new ConversationSession('panels', client);
  sessions.push(session);
  return { fake, session };
}

const panelReads = (fake: ReturnType<typeof setup>['fake']) =>
  fake.serverCall.mock.calls.map(([name]) => name).filter((name) => name !== 'slash.list');

it('reads only the product panels the runtime offers', async () => {
  const { fake, session } = setup(['goal.status', 'workers.list']);
  await session.refreshPanels();
  expect(panelReads(fake).sort()).toEqual(['goal.status', 'workers.list']);
  expect(fake.listQueue).toHaveBeenCalled();
});

it('asks a plain agent for none of them', async () => {
  const { fake, session } = setup([]);
  await session.refreshPanels();
  expect(panelReads(fake)).toEqual([]);
  expect(session.panels.state.get().goalSnapshot).toBeNull();
});
