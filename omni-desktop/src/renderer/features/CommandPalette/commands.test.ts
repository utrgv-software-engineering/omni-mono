import { describe, expect, it, vi } from 'vitest';

import type { CodeTab } from '@/shared/types';

import { buildCommands, filterCommands } from './commands';

const tab = (patch: Partial<CodeTab>): CodeTab => ({
  id: 'tab-1',
  projectId: null,
  createdAt: 1,
  ...patch,
});

const ctx = (codeTabs: CodeTab[] = []) => ({
  codeTabs,
  resolveTabLabel: (t: CodeTab) => `label:${t.id}`,
  navigate: vi.fn(),
  activateColumn: vi.fn(),
  goToRoutines: vi.fn(),
  createProject: vi.fn(),
  newSession: vi.fn(),
  toggleViewportDebug: vi.fn(),
});

describe('buildCommands', () => {
  it('includes navigation, session, and per-column commands', () => {
    const c = ctx([tab({ id: 'a', projectId: 'p1' })]);
    const commands = buildCommands(c);
    const ids = commands.map((cmd) => cmd.id);
    expect(ids).toEqual(
      expect.arrayContaining(['nav-work', 'nav-chat', 'nav-settings', 'create-project', 'new-session', 'column-a'])
    );
    expect(ids).not.toContain('toggle-layout');
  });

  it('numbers column hints', () => {
    const commands = buildCommands(ctx([tab({ id: 'a', projectId: 'p1' }), tab({ id: 'b', projectId: 'p2' })]));
    expect(commands.find((cmd) => cmd.id === 'column-b')!.hint).toBe('⌘2');
  });

  it('runs the bound actions', () => {
    const c = ctx([tab({ id: 'a', projectId: 'p1' })]);
    const commands = buildCommands(c);
    commands.find((cmd) => cmd.id === 'nav-work')!.run();
    expect(c.navigate).toHaveBeenCalledWith('work');
    commands.find((cmd) => cmd.id === 'nav-routines')!.run();
    expect(c.goToRoutines).toHaveBeenCalled();
    commands.find((cmd) => cmd.id === 'create-project')!.run();
    expect(c.createProject).toHaveBeenCalled();
    commands.find((cmd) => cmd.id === 'column-a')!.run();
    expect(c.activateColumn).toHaveBeenCalledWith('a');
  });
});

describe('filterCommands', () => {
  const commands = buildCommands(ctx([tab({ id: 'a', projectId: 'p1' })]));

  it('returns all commands for an empty query', () => {
    expect(filterCommands(commands, '  ')).toHaveLength(commands.length);
  });

  it('matches labels case-insensitively', () => {
    expect(filterCommands(commands, 'SETTINGS').map((c) => c.id)).toEqual(['nav-settings']);
  });

  it('matches keywords', () => {
    expect(filterCommands(commands, 'kanban board').map((c) => c.id)).toEqual([]);
    expect(filterCommands(commands, 'folders').map((c) => c.id)).toEqual(['nav-work']);
  });
});
