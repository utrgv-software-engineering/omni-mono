import { describe, expect, it } from 'vitest';

import { LineBuffer } from '@/lib/line-buffer';

describe('LineBuffer', () => {
  it('should initialize with an empty buffer', () => {
    const buffer = new LineBuffer();
    expect(buffer.flush()).toBe('');
  });

  it('should append data and return complete lines', () => {
    const buffer = new LineBuffer();
    expect(buffer.append('line1\nline2\n')).toEqual(['line1', 'line2']);
    expect(buffer.flush()).toBe('');
  });

  it('should retain incomplete lines in the buffer', () => {
    const buffer = new LineBuffer();
    expect(buffer.append('line1\nincomplete')).toEqual(['line1']);
    expect(buffer.flush()).toBe('incomplete');
  });

  it('should handle appending empty strings', () => {
    const buffer = new LineBuffer();
    expect(buffer.append('')).toEqual([]);
    expect(buffer.flush()).toBe('');
  });

  it('should handle strings without newline characters', () => {
    const buffer = new LineBuffer();
    expect(buffer.append('no-newline')).toEqual([]);
    expect(buffer.flush()).toBe('no-newline');
  });

  it('strips ANSI escape codes when configured', () => {
    const buffer = new LineBuffer({ stripAnsi: true });
    expect(buffer.append('\u001b[31mred\u001b[0m'));
    expect(buffer.flush()).toBe('red');
  });
});
