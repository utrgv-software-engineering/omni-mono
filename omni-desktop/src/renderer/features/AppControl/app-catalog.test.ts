import { describe, expect, it } from 'vitest';

import { buildAppCatalog, type CatalogInput } from '@/renderer/features/AppControl/app-catalog-core';
import type { LiveAppSnapshot } from '@/shared/app-control-types';
import { makeAppHandleId } from '@/shared/app-control-types';
import { APP_REGISTRY } from '@/shared/app-registry';
import type { CodeTab } from '@/shared/types';

const tab = (id: string, extra?: Partial<CodeTab>): CodeTab => ({
  id,
  projectId: null,
  createdAt: 0,
  ...extra,
});

const liveColumn = (tabId: string, appId: string): LiveAppSnapshot => ({
  handleId: makeAppHandleId('column', appId, tabId),
  appId,
  kind: 'builtin-browser',
  scope: 'column',
  tabId,
  label: appId,
  controllable: true,
});

const base = (over: Partial<CatalogInput>): CatalogInput => ({
  filter: { allowGlobal: true },
  live: {},
  registry: APP_REGISTRY,
  codeTabs: [tab('t1')],
  ...over,
});

describe('buildAppCatalog', () => {
  it('reports column-scoped apps as available even when none are running', () => {
    const cat = buildAppCatalog(base({ filter: { tabId: 't1', allowGlobal: true } }));
    const terminal = cat.find((a) => a.id === 'terminal' && a.tabId === 't1');
    expect(terminal).toBeDefined();
    expect(terminal!.available).toBe(true);
    expect(terminal!.running).toBe(false);
    expect(terminal!.handleId).toBe('tab-t1:terminal');
  });

  it('lists the native Files surface without claiming separate app-control support', () => {
    const cat = buildAppCatalog(base({ filter: { tabId: 't1', allowGlobal: true } }));
    const files = cat.find((app) => app.id === 'files' && app.tabId === 't1');
    expect(files).toMatchObject({ available: true, controllable: false, kind: 'builtin-files', running: false });
  });

  it('lists the native Git surface without claiming separate app-control support', () => {
    const cat = buildAppCatalog(base({ filter: { tabId: 't1', allowGlobal: true } }));
    const git = cat.find((app) => app.id === 'git' && app.tabId === 't1');
    expect(git).toMatchObject({ available: true, controllable: false, kind: 'builtin-git', running: false });
  });

  it('excludes the agent chat surface from the catalog', () => {
    const cat = buildAppCatalog(base({ filter: { tabId: 't1', allowGlobal: true } }));
    expect(cat.some((a) => a.id === 'chat')).toBe(false);
  });

  it('flags running apps from the live registry', () => {
    const cat = buildAppCatalog(
      base({
        filter: { tabId: 't1', allowGlobal: true },
        live: { 'tab-t1:browser': liveColumn('t1', 'browser') },
      })
    );
    const browser = cat.find((a) => a.id === 'browser' && a.tabId === 't1');
    expect(browser!.running).toBe(true);
  });

  it('column caller does not see other columns', () => {
    const cat = buildAppCatalog(base({ filter: { tabId: 't1', allowGlobal: true }, codeTabs: [tab('t1'), tab('t2')] }));
    expect(cat.some((a) => a.tabId === 't2')).toBe(false);
  });
});
