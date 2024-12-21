import { describe, expect, it } from 'vitest';

import { SlidingBuffer } from '@/lib/sliding-buffer';

describe('SlidingBuffer', () => {
  it('should initialize with an empty buffer', () => {
    const buffer = new SlidingBuffer<number>(3);
    expect(buffer.get()).toEqual([]);
  });

  it('should initialize with an initial buffer', () => {
    const buffer = new SlidingBuffer<number>(3, [1, 2]);
    expect(buffer.get()).toEqual([1, 2]);
  });

  it('should add elements to the buffer', () => {
    const buffer = new SlidingBuffer<number>(3);
    buffer.push(1);
    buffer.push(2);
    expect(buffer.get()).toEqual([1, 2]);
  });

  it('should remove the oldest element when the buffer exceeds its size', () => {
    const buffer = new SlidingBuffer<number>(3);
    buffer.push(1);
    buffer.push(2);
    buffer.push(3);
    buffer.push(4);
    expect(buffer.get()).toEqual([2, 3, 4]);
  });

  it('should handle different types of elements', () => {
    const buffer = new SlidingBuffer<string>(2);
    buffer.push('a');
    buffer.push('b');
    buffer.push('c');
    expect(buffer.get()).toEqual(['b', 'c']);
  });

  it('should handle buffer size of 1', () => {
    const buffer = new SlidingBuffer<number>(1);
    buffer.push(1);
    buffer.push(2);
    expect(buffer.get()).toEqual([2]);
  });

  it('should handle buffer size of 0', () => {
    const buffer = new SlidingBuffer<number>(0);
    buffer.push(1);
    expect(buffer.get()).toEqual([]);
  });
});
