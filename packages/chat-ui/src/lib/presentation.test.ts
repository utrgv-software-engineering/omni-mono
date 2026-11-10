import { describe, expect, it } from 'vitest';
import { PresentationGate, presentationFromArtifact, type Presentation } from './presentation';
const view: Presentation = {kind:'artifact', id:'abc', title:'Plan', presentation_id:'one', issued_at:101};
describe('explicit viewer presentation', () => {
  it('ignores ordinary artifacts and rejects navigation payloads', () => {
    expect(presentationFromArtifact({mode:'markdown',content:'# Plan'})).toBeNull();
    expect(presentationFromArtifact({mode:'workplace_view',content:JSON.stringify({...view,id:'../../other-factory'})})).toBeNull();
    expect(presentationFromArtifact({mode:'workplace_view',content:JSON.stringify(view)})).toEqual(view);
  });
  it('does not reopen history, replay, or a background chat', () => {
    const gate = new PresentationGate(100);
    expect(gate.accept({...view, issued_at:99},true)).toBe(false);
    expect(gate.accept({...view,presentation_id:'new'},true)).toBe(true);
    expect(gate.accept({...view,presentation_id:'new'},true)).toBe(false);
    expect(gate.accept({...view,presentation_id:'background'},false)).toBe(false);
    expect(gate.accept({...view,presentation_id:'background'},true)).toBe(false);
    expect(new PresentationGate(110).accept(view,true)).toBe(false);
  });
});
