import { expect, it } from 'vitest';

import type { MessageItem } from './chat-types';
import { visibleVoiceTranscript } from './voice-captions';
const live = {
  type: 'chat',
  role: 'user',
  content: 'Spoken words',
  voice_caption: true,
  item_id: 'live:call:1',
} as MessageItem;
const saved = {
  type: 'chat',
  role: 'user',
  content: 'Spoken words',
  canonical: { source_ref: { voice_caption: true, live_call: 'call', voice_id: '1' } },
} as MessageItem;
const result = { type: 'chat', role: 'assistant', content: 'Task completed' } as MessageItem;
const approval = { type: 'approval', request_id: 'approval' } as MessageItem;
it('hides live and persisted speech without hiding results or approval cards', () => {
  const source = [live, saved, result, approval];
  expect(visibleVoiceTranscript(source, false)).toEqual([result, approval]);
  expect(source).toHaveLength(4);
});
it('shows saved speech once when its live counterpart is still present', () => {
  expect(visibleVoiceTranscript([saved, live, result], true)).toEqual([saved, result]);
  expect(visibleVoiceTranscript([saved, result], true)).toEqual([saved, result]);
});
it('does not treat a delegated task or tool result as captions', () => {
  const delegated = {
    ...result,
    canonical: { source_ref: { event: 'enqueue_message', source: 'voice:codex-live' } },
  } as MessageItem;
  expect(visibleVoiceTranscript([delegated], false)).toEqual([delegated]);
});

const envelope =
  '[Live voice request item_example]\nAct on the request below through your normal tools and approvals. Return concise verified results in text; the Live voice model speaks them. Do not call speak or say. Transcripts may contain corrections; use the latest intent.\n\n';
it('displays only the delegated request with captions on or off, without changing history', () => {
  const request = { type: 'chat', role: 'user', content: `${envelope}[emm] What repos are connected?` } as MessageItem;
  for (const captions of [true, false]) {
    expect(visibleVoiceTranscript([request, result, approval], captions)).toEqual([
      { ...request, content: '[emm] What repos are connected?' },
      result,
      approval,
    ]);
  }
  expect(request).toMatchObject({ content: `${envelope}[emm] What repos are connected?` });
});
it('leaves assistant quotations and incomplete or ordinary user text unchanged', () => {
  const messages = [
    { type: 'chat', role: 'assistant', content: `${envelope}Example` },
    { type: 'chat', role: 'user', content: '[Live voice request example]\nUser-authored text' },
    { type: 'chat', role: 'user', content: `Explain this: ${envelope}` },
  ] as MessageItem[];
  expect(visibleVoiceTranscript(messages, false)).toEqual(messages);
});

function caption(id: string, text: string, role = 'user', call = 'call'): MessageItem {
  return {
    type: 'chat',
    role,
    content: text,
    canonical: { source_ref: { voice_caption: true, live_call: call, voice_id: id } },
  } as MessageItem;
}
function request(text: string): MessageItem {
  return { type: 'chat', role: 'user', content: envelope + text } as MessageItem;
}
it('restores fragmented speech even when the final caption arrives after delegation', () => {
  const task = request('[emm]\nmanagement, leads, all of that stuff');
  const rows = [
    result,
    caption('1', '[emm]\nThe Chatbot SaaS repo is'),
    caption('2', 'Mm-hmm', 'assistant'),
    caption('3', '[emm]\nactually Indygym. It manages billing,'),
    task,
    result,
    caption('4', '[emm]\ncustomer management, leads, all of that stuff'),
    approval,
  ];
  const original = JSON.stringify(rows);
  for (const enabled of [false, true]) {
    const visible = visibleVoiceTranscript(rows, enabled);
    expect(visible.filter((r) => r.type === 'chat' && r.role === 'user')).toEqual([
      {
        ...task,
        content:
          '[emm]\nThe Chatbot SaaS repo is actually Indygym. It manages billing, customer management, leads, all of that stuff',
      },
    ]);
    expect(visible).toContain(approval);
    expect(visible.filter((r) => r === result)).toHaveLength(2);
  }
  expect(JSON.stringify(rows)).toBe(original);
});
it('does not include a previous request, another caller, or another speaker', () => {
  const task = request('[emm]\n, scoreboard, all that stuff');
  const rows = [
    caption('1', '[emm]\nOld request'),
    result,
    caption('2', '[emm]\nEarlier call', 'user', 'old'),
    caption('3', '[other]\nSomeone else'),
    caption('4', '[emm]\nJiu-jitsu Pro manages broadcasts'),
    task,
    caption('5', '[emm]\n, scoreboard, all that stuff'),
  ];
  expect(visibleVoiceTranscript(rows, false).at(-1)).toEqual({
    ...task,
    content: '[emm]\nJiu-jitsu Pro manages broadcasts, scoreboard, all that stuff',
  });
});
it('deduplicates live and saved fragments and reconstructs the same text after reload', () => {
  const a = caption('1', '[emm]\nRemember that Jiu-jitsu Pro');
  const b = caption('2', '[emm]\nruns our events');
  const task = request('[emm]\nruns our events');
  const liveA = {
    type: 'chat',
    role: 'user',
    content: 'Remember that Jiu-jitsu Pro',
    voice_caption: true,
    item_id: 'live:call:1',
  } as MessageItem;
  const saved = [result, a, task, b];
  const liveB = {
    type: 'chat',
    role: 'user',
    content: 'runs our events',
    voice_caption: true,
    item_id: 'live:call:2',
  } as MessageItem;
  expect(
    visibleVoiceTranscript([result, liveA, task, liveB], false).map((r) => (r.type === 'chat' ? r.content : r))
  ).toEqual(visibleVoiceTranscript(saved, false).map((r) => (r.type === 'chat' ? r.content : r)));
  expect(visibleVoiceTranscript([result, a, liveA, task, b], false)).toEqual(visibleVoiceTranscript(saved, false));
  expect(visibleVoiceTranscript(saved, false).at(-1)).toEqual({
    ...task,
    content: '[emm]\nRemember that Jiu-jitsu Pro runs our events',
  });
});
it('keeps unmatched delegation text and does not borrow a later request', () => {
  const task = request('[emm]\nCreate the project');
  const rows = [
    caption('1', '[emm]\nUnrelated speech'),
    task,
    request('[emm]\nAnother task'),
    caption('2', '[emm]\nCreate the project'),
  ];
  expect(visibleVoiceTranscript(rows, false)[0]).toEqual({ ...task, content: '[emm]\nCreate the project' });
});
it('does not reuse a late caption in the next queued delegation', () => {
  const first = request('[emm]\nfirst request');
  const second = request('[emm]\nsecond request');
  const rows = [
    first,
    caption('1', '[emm]\nfirst request'),
    caption('2', '[emm]\nHere is my'),
    second,
    caption('3', '[emm]\nsecond request'),
  ];
  expect(visibleVoiceTranscript(rows, false)).toEqual([
    { ...first, content: '[emm]\nfirst request' },
    { ...second, content: '[emm]\nHere is my second request' },
  ]);
});
it('does not match a later call when the current call has no matching caption', () => {
  const task = request('[emm]\nremember this');
  expect(
    visibleVoiceTranscript(
      [
        caption('1', '[emm]\nCurrent speech'),
        task,
        caption('1', '[emm]\nSomething else to remember this', 'user', 'next'),
      ],
      false
    )
  ).toEqual([{ ...task, content: '[emm]\nremember this' }]);
});
