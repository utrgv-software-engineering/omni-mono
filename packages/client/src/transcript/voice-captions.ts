import type { MessageItem } from './chat-types';

// Match the generated delegation envelope, not arbitrary quoted user text.
const liveRequestEnvelope =
  /^\[Live voice request [^\]\r\n]+\]\nAct on the request below through your normal tools and approvals\. Return concise verified results in text; the Live voice model speaks them\. Do not call speak or say\. Transcripts may contain corrections; use the latest intent\.\n\n/;

export function isVoiceCaption(item: MessageItem): boolean {
  return item.type === 'chat' && (item.voice_caption === true || item.canonical?.source_ref.voice_caption === true);
}

function voiceIdentity(item: MessageItem): { call: string; key: string } | undefined {
  if (item.type !== 'chat') {
    return;
  }
  const source = item.canonical?.source_ref;
  if (source?.live_call && source?.voice_id) {
    return { call: String(source.live_call), key: `live:${source.live_call}:${source.voice_id}` };
  }
  const match = item.item_id?.match(/^live:(.+):([^:]+)$/);
  if (match) {
    return { call: match[1], key: item.item_id! };
  }
}

function speech(text: string) {
  const match = text.match(/^\[([^\]\n]+)\]\s*/);
  return { speaker: match?.[1] ?? '', text: match ? text.slice(match[0].length) : text };
}

const normalized = (text: string) => text.replace(/\s+/g, ' ').trim();

/** Recover the spoken request from captions, never from assistant paraphrases.
 * Live may delegate just the tail of a request. Its final caption can arrive
 * after the delegation, so match that tail before using preceding fragments.
 * Never cross another task, call, or speaker. Unmatched handoffs stay intact.
 */
function spokenRequest(items: MessageItem[], index: number, request: string, usedCaptions: Set<number>) {
  const target = speech(request);
  if (!normalized(target.text)) {
    return;
  }
  let start = index;
  while (start > 0) {
    const previous = items[start - 1];
    if (previous.type === 'chat' && !isVoiceCaption(previous)) {
      break;
    }
    start--;
  }
  const fragments: { index: number; text: string }[] = [];
  let call = '';
  for (let i = start; i < items.length; i++) {
    const item = items[i];
    if (i === index) {
      continue;
    }
    if (usedCaptions.has(i)) {
      fragments.length = 0;
      continue;
    }
    if (item.type !== 'chat') {
      continue;
    }
    if (!isVoiceCaption(item)) {
      // Later assistant output may race the final caption. A new user task
      // always ends the search; preceding task output bounded `start` above.
      if (i > index && item.role === 'user') {
        break;
      }
      continue;
    }
    const identity = voiceIdentity(item);
    if (!identity) {
      continue;
    }
    if (call && call !== identity.call) {
      if (i > index) {
        break;
      }
      fragments.length = 0;
    }
    call = identity.call;
    if (item.role !== 'user') {
      continue;
    }
    const fragment = speech(item.content);
    // Live captions lack the server-added attribution until persisted.
    if (fragment.speaker && fragment.speaker !== target.speaker) {
      fragments.length = 0;
      continue;
    }
    fragments.push({ index: i, text: fragment.text });
    const text = fragments
      .map((f) => f.text)
      .join(' ')
      .replace(/\s+([,.!?;:])/g, '$1');
    if (normalized(text).endsWith(normalized(target.text))) {
      return {
        content: `${target.speaker ? `[${target.speaker}]\n` : ''}${text.trim()}`,
        captions: fragments.map((f) => f.index),
      };
    }
  }
}

/** Display only; source history and the model's memory are never modified. */
export function visibleVoiceTranscript(items: MessageItem[], captions: boolean): MessageItem[] {
  const seen = new Set<string>();
  const unique = items.filter((item) => {
    if (!isVoiceCaption(item)) {
      return true;
    }
    const key = voiceIdentity(item)?.key ?? (item.type === 'chat' ? item.item_id : undefined);
    if (!key) {
      return true;
    }
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
  const usedCaptions = new Set<number>();
  const displayed = unique.map((item, index) => {
    if (item.type !== 'chat' || item.role !== 'user' || !liveRequestEnvelope.test(item.content)) {
      return item;
    }
    const request = item.content.replace(liveRequestEnvelope, '');
    const spoken = spokenRequest(unique, index, request, usedCaptions);
    spoken?.captions.forEach((i) => usedCaptions.add(i));
    return { ...item, content: spoken?.content ?? request };
  });
  return displayed.filter((item, index) => !isVoiceCaption(item) || (captions && !usedCaptions.has(index)));
}
