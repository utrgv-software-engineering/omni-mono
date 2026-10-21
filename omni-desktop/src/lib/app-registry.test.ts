import { describe, expect, it } from 'vitest';

import { APP_REGISTRY, BUILTIN_APPS } from '@/shared/app-registry';

describe('APP_REGISTRY', () => {
  it('lists every builtin app in display order', () => {
    expect(APP_REGISTRY).toHaveLength(BUILTIN_APPS.length);
    const orders = APP_REGISTRY.map((app) => app.order);
    expect(orders).toEqual([...orders].sort((a, b) => a - b));
  });

  it('marks every builtin app as columnScoped', () => {
    for (const app of BUILTIN_APPS) {
      expect(app.columnScoped).toBe(true);
    }
  });
});
