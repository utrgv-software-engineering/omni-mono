import { describe, expect, it } from 'vitest';

import { withResultAsync } from '@/lib/result';

const promiseify = <T>(fn: () => T): (() => Promise<T>) => {
  return () =>
    new Promise((resolve) => {
      resolve(fn());
    });
};

describe('Result Utility Functions', () => {
  it('withResultAsync() should return Ok on success', async () => {
    const fn = promiseify(() => 42);
    const result = await withResultAsync(fn);
    expect(result.isOk()).toBe(true);
    if (result.isOk()) {
      expect(result.value).toBe(42);
    }
  });

  it('withResultAsync() should return Err on exception', async () => {
    const fn = promiseify(() => {
      throw new Error('Async failure');
    });
    const result = await withResultAsync(fn);
    expect(result.isErr()).toBe(true);
    if (result.isErr()) {
      expect(result.error.message).toBe('Async failure');
    }
  });
});
