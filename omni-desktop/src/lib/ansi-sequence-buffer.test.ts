import { describe, expect, it } from 'vitest';

import { AnsiSequenceBuffer } from '@/lib/ansi-sequence-buffer';

describe('AnsiBuffer', () => {
  it('handles complete ANSI sequences', () => {
    const buffer = new AnsiSequenceBuffer();
    const result = buffer.append('\x1b[31mHello\x1b[0m');

    expect(result.complete).toBe('\x1b[31mHello\x1b[0m');
    expect(result.hasIncomplete).toBe(false);
  });

  it('buffers incomplete ANSI sequences', () => {
    const buffer = new AnsiSequenceBuffer();
    const result = buffer.append('\x1b[31mHello\x1b[');

    expect(result.complete).toBeNull();
    expect(result.hasIncomplete).toBe(true);
  });

  it('combines buffered content with new complete sequence', () => {
    const buffer = new AnsiSequenceBuffer();
    buffer.append('\x1b[31mHello\x1b[');
    const result = buffer.append('0m');

    expect(result.complete).toBe('\x1b[31mHello\x1b[0m');
    expect(result.hasIncomplete).toBe(false);
  });

  it('handles text without ANSI sequences', () => {
    const buffer = new AnsiSequenceBuffer();
    const result = buffer.append('Plain text');

    expect(result.complete).toBe('Plain text');
    expect(result.hasIncomplete).toBe(false);
  });
});
