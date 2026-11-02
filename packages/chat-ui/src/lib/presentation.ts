import type { ArtifactItem } from '@omniagents/client/transcript/chat-types';

export type Presentation = {
  kind: 'artifact' | 'canvas' | 'desktop' | 'activity';
  title: string;
  id?: string;
  board?: string;
  presentation_id: string;
  issued_at: number;
};

export function presentationFromArtifact(item: Pick<ArtifactItem, 'mode' | 'content'>): Presentation | null {
  if (item.mode !== 'workplace_view') return null;
  try {
    const value = JSON.parse(item.content);
    if (!['artifact', 'canvas', 'desktop'].includes(value.kind) || typeof value.title !== 'string' ||
        typeof value.presentation_id !== 'string' || !Number.isFinite(value.issued_at)) return null;
    if (value.kind !== 'desktop' && (typeof value.id !== 'string' || !/^[\w-]+$/.test(value.id))) return null;
    if (value.board !== undefined && typeof value.board !== 'string') return null;
    return value;
  } catch { return null; }
}

/** Only new presentations on this live connection may move the viewer. */
export class PresentationGate {
  private seen = new Set<string>();
  constructor(private since: number) {}
  accept(value: Presentation, active: boolean) {
    if (this.seen.has(value.presentation_id)) return false;
    this.seen.add(value.presentation_id);
    return active && value.issued_at >= this.since;
  }
}
