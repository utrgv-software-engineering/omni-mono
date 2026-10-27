/**
 * Tests for the launcher-only client-tool definitions and `buildSessionVariables`.
 */

import { describe, expect, it } from 'vitest';

import { buildSessionVariables, extractSafeToolNames } from '@/lib/client-tools';

describe('client_tools shape', () => {
  it('chat surface contains the launcher-only tools, no code-deck-only tools', () => {
    const vars = buildSessionVariables({ surface: 'chat' }) as {
      client_tools: { name: string }[];
      additional_instructions: string;
    };
    const names = vars.client_tools.map((t) => t.name);
    expect(names).toContain('display_plan');
    expect(names).toContain('browser_list_tabsets');
    // Code-only tools should NOT be in chat surface
    expect(names).not.toContain('browser_open');
    expect(vars.additional_instructions).toContain('## Driving apps');
  });

  it('code surface includes code-deck-only tools', () => {
    const vars = buildSessionVariables({ surface: 'code' }) as {
      client_tools: { name: string }[];
    };
    const names = vars.client_tools.map((t) => t.name);
    expect(names).toContain('browser_open');
    expect(names).toContain('display_plan');
    expect(names).toContain('browser_list_tabsets');
  });

  it('launch_app is available on the code surface, not chat', () => {
    const code = buildSessionVariables({ surface: 'code' }) as { client_tools: { name: string }[] };
    const chat = buildSessionVariables({ surface: 'chat' }) as { client_tools: { name: string }[] };
    expect(code.client_tools.map((t) => t.name)).toContain('launch_app');
    expect(chat.client_tools.map((t) => t.name)).not.toContain('launch_app');
  });

  it('workspace tools are absent from chat and code surfaces', () => {
    for (const surface of ['chat', 'code'] as const) {
      const names = (buildSessionVariables({ surface }) as { client_tools: { name: string }[] }).client_tools.map(
        (t) => t.name
      );
      expect(names).not.toContain('list_workspace');
      expect(names).not.toContain('column_send');
    }
  });

  it('interactive mode uses safe_tool_names (allowlist of read-only tools)', () => {
    const vars = buildSessionVariables({ surface: 'chat' }) as {
      safe_tool_overrides: { safe_tool_names?: string[]; safe_tool_patterns?: string[] };
    };
    const overrides = vars.safe_tool_overrides;
    expect(overrides.safe_tool_names).toBeDefined();
    expect(overrides.safe_tool_patterns).toBeUndefined();
    const safeNames = overrides.safe_tool_names!;
    // App-control snapshot/list tools are read-only and stay safe
    expect(safeNames).toContain('list_apps');
    expect(safeNames).toContain('app_snapshot');
    // Mutating launcher tools must require approval
    expect(safeNames).not.toContain('escalate');
  });

  it('context identifiers are present when provided', () => {
    const vars = buildSessionVariables({
      surface: 'chat',
      context: { projectId: 'proj-1', projectLabel: 'My Project' },
    }) as { additional_instructions: string };
    expect(vars.additional_instructions).toContain('My Project');
    expect(vars.additional_instructions).toContain('proj-1');
  });

  it('plain-folder sources get deliverables-in-the-folder guidance, no artifacts channel', () => {
    const vars = buildSessionVariables({
      surface: 'code',
      context: {
        projectId: 'proj-1',
        sources: [{ id: 's1', mountName: 'notes', kind: 'local', workspaceDir: '/home/u/notes' }],
      },
    }) as { additional_instructions: string };
    expect(vars.additional_instructions).toContain('## Where to put output for the user');
    expect(vars.additional_instructions).toContain('`/workspace/notes/`');
    expect(vars.additional_instructions).not.toContain('.omni-artifacts');
    expect(vars.additional_instructions).not.toContain('PR_TITLE');
  });

  it('repo sources get keep-the-repo-clean guidance', () => {
    const vars = buildSessionVariables({
      surface: 'code',
      context: {
        projectId: 'proj-1',
        sources: [{ id: 's1', mountName: 'lib', kind: 'git-remote', repoUrl: 'https://github.com/acme/lib' }],
      },
    }) as { additional_instructions: string };
    expect(vars.additional_instructions).toContain('share results in your reply');
    expect(vars.additional_instructions).not.toContain('.omni-artifacts');
  });

  it('a bare workspaceDir (chat scratch) gets working-folder guidance', () => {
    const vars = buildSessionVariables({
      surface: 'chat',
      context: { workspaceDir: '/home/u/Omni/Workspace/Sessions/abc' },
    }) as { additional_instructions: string };
    expect(vars.additional_instructions).toContain('## Where to put output for the user');
    expect(vars.additional_instructions).toContain('working folder');
    expect(vars.additional_instructions).not.toContain('.omni-artifacts');
  });

  it('renders the workspace layout when sources are present', () => {
    const vars = buildSessionVariables({
      surface: 'code',
      context: {
        projectId: 'proj-1',
        sources: [
          { id: 's1', mountName: 'launcher', kind: 'local', workspaceDir: '/home/emm/Omni/Workspace/launcher' },
          { id: 's2', mountName: 'omni-code', kind: 'local', workspaceDir: '/home/emm/Omni/Workspace/omni-code' },
          {
            id: 's3',
            mountName: 'omniagents',
            kind: 'git-remote',
            repoUrl: 'https://github.com/anthropic/omniagents',
            defaultBranch: 'main',
          },
        ],
      },
    }) as { additional_instructions: string };
    expect(vars.additional_instructions).toContain('## Workspace Layout');
    expect(vars.additional_instructions).toContain('3 sources co-mounted');
    expect(vars.additional_instructions).toContain('`/workspace/launcher/`');
    expect(vars.additional_instructions).toContain(
      '`/workspace/omniagents/` — https://github.com/anthropic/omniagents@main (git-remote)'
    );
  });

  it('omits the workspace layout when sources is empty', () => {
    const vars = buildSessionVariables({
      surface: 'code',
      context: { projectId: 'proj-1', sources: [] },
    }) as { additional_instructions: string };
    expect(vars.additional_instructions).not.toContain('## Workspace Layout');
  });

  it('extractSafeToolNames returns only tools with safe: true', () => {
    const tools = [
      { name: 'read_thing', safe: true, description: '', parameters: { type: 'object', properties: {} } },
      { name: 'write_thing', description: '', parameters: { type: 'object', properties: {} } },
      { name: 'list_thing', safe: true, description: '', parameters: { type: 'object', properties: {} } },
    ] as const;
    expect(extractSafeToolNames(tools)).toEqual(['read_thing', 'list_thing']);
  });
});
