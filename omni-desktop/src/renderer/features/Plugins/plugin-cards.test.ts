import { describe, expect, it } from 'vitest';

import type { BundleUpdateInfo, MarketplaceManifest, McpConfig, McpServerEntry, SkillEntry } from '@/shared/types';

import {
  buildExplorePlugins,
  buildInstalledPlugins,
  bundleUpdateKey,
  collectDriftedItems,
  connectorNeedsUpdate,
  displayName,
  filterPlugins,
  formatPluginName,
  isBundleInstalled,
  mergeConnectorUpdate,
} from './plugin-cards';

const skill = (overrides: Partial<SkillEntry> = {}): SkillEntry => ({
  name: 'git-workflow',
  description: 'Git helpers',
  path: '/skills/git-workflow',
  enabled: true,
  source: { kind: 'marketplace', repo: 'omni/official', plugin: 'dev-tools', ref: 'main' },
  ...overrides,
});

const update = (overrides: Partial<BundleUpdateInfo> = {}): BundleUpdateInfo => ({
  bundleKey: 'omni/official:dev-tools',
  repo: 'omni/official',
  plugin: 'dev-tools',
  status: 'update-available',
  addedSkills: ['new-skill'],
  removedSkills: [],
  ...overrides,
});

const mcpConfig = (servers: McpConfig['mcpServers'] = {}): McpConfig => ({ mcpServers: servers });

const manifest = (overrides: Partial<MarketplaceManifest> = {}): MarketplaceManifest => ({
  name: 'Test Marketplace',
  plugins: [],
  ...overrides,
});

describe('formatPluginName', () => {
  it('title-cases kebab-case ids', () => {
    expect(formatPluginName('git-workflow')).toBe('Git Workflow');
    expect(formatPluginName('a')).toBe('A');
  });
});

describe('buildInstalledPlugins', () => {
  it('merges both sources ordered by kind then name', () => {
    const items = buildInstalledPlugins({
      mcpConfig: mcpConfig({ github: { type: 'stdio', command: 'gh-mcp' } }),
      skills: [skill()],
      updates: {},
    });
    expect(items.map((i) => i.kind)).toEqual(['connector', 'skill']);
  });

  it('returns empty for empty inputs', () => {
    expect(buildInstalledPlugins({ mcpConfig: null, skills: [], updates: {} })).toEqual([]);
  });

  it('surfaces runtime-only managed servers with read-only metadata', () => {
    const items = buildInstalledPlugins({
      mcpConfig: null,
      skills: [],
      updates: {},
      runtimeMcpServers: [
        {
          server_name: 'omni-projects',
          source: 'host_managed',
          transport: 'stdio',
          params: { command: 'omni-projects', args: [] },
          server_options: {},
          enabled: true,
          read_only: true,
          read_only_reason: 'host_managed',
          status: { state: 'ready' },
          auth: { kind: 'none', state: 'not_required' },
        },
      ],
    });
    expect(items).toMatchObject([
      {
        kind: 'connector',
        id: 'omni-projects',
        runtime: { read_only: true, source: 'host_managed' },
        server: { command: 'omni-projects' },
      },
    ]);
  });

  it('attaches update-available reports to marketplace skills by bundle key', () => {
    const items = buildInstalledPlugins({
      mcpConfig: null,
      skills: [skill(), skill({ name: 'local-skill', source: { kind: 'local' } })],
      updates: { [bundleUpdateKey('omni/official', 'dev-tools')]: update() },
    });
    const [marketplaceSkill, localSkill] = items;
    expect(marketplaceSkill).toMatchObject({ kind: 'skill', update: { plugin: 'dev-tools' } });
    expect(localSkill).toMatchObject({ kind: 'skill', update: undefined });
  });

  it('ignores up-to-date reports', () => {
    const items = buildInstalledPlugins({
      mcpConfig: null,
      skills: [skill()],
      updates: { [bundleUpdateKey('omni/official', 'dev-tools')]: update({ status: 'up-to-date' }) },
    });
    expect(items[0]).toMatchObject({ update: undefined });
  });
});

describe('isBundleInstalled', () => {
  it('matches only marketplace skills from the same repo and plugin', () => {
    const skills = [skill()];
    expect(isBundleInstalled(skills, 'omni/official', 'dev-tools')).toBe(true);
    expect(isBundleInstalled(skills, 'omni/official', 'other')).toBe(false);
    expect(isBundleInstalled(skills, 'other/repo', 'dev-tools')).toBe(false);
    expect(isBundleInstalled([skill({ source: { kind: 'local' } })], 'omni/official', 'dev-tools')).toBe(false);
  });
});

describe('connectorNeedsUpdate', () => {
  it('is false when transport matches, regardless of env values', () => {
    const def: McpServerEntry = { type: 'stdio', command: 'gh-mcp', args: ['--x'], env: { TOKEN: '' } };
    const existing: McpServerEntry = { type: 'stdio', command: 'gh-mcp', args: ['--x'], env: { TOKEN: 'secret' } };
    expect(connectorNeedsUpdate(existing, def)).toBe(false);
  });

  it('detects transport drift (command, args, url, type)', () => {
    const base: McpServerEntry = { type: 'stdio', command: 'gh-mcp', args: [] };
    expect(connectorNeedsUpdate({ ...base, command: 'old-mcp' }, base)).toBe(true);
    expect(connectorNeedsUpdate({ ...base, args: ['--old'] }, base)).toBe(true);
    expect(connectorNeedsUpdate({ type: 'http', url: 'https://a' }, { type: 'http', url: 'https://b' })).toBe(true);
    expect(connectorNeedsUpdate({ type: 'sse', url: 'https://a' }, { type: 'http', url: 'https://a' })).toBe(true);
  });

  it('treats a missing type as stdio', () => {
    expect(connectorNeedsUpdate({ command: 'x' }, { type: 'stdio', command: 'x' })).toBe(false);
  });

  it('detects env/header keys the definition added', () => {
    const def: McpServerEntry = { type: 'stdio', command: 'x', env: { TOKEN: '', REGION: '' } };
    expect(connectorNeedsUpdate({ type: 'stdio', command: 'x', env: { TOKEN: 'set' } }, def)).toBe(true);
  });
});

describe('mergeConnectorUpdate', () => {
  it('takes the definition transport but preserves user env values and adds new keys', () => {
    const existing: McpServerEntry = { type: 'stdio', command: 'old', args: ['--old'], env: { TOKEN: 'secret' } };
    const def: McpServerEntry = { type: 'stdio', command: 'new', args: ['--new'], env: { TOKEN: '', REGION: 'us' } };
    const merged = mergeConnectorUpdate(existing, def);
    expect(merged).toEqual({ type: 'stdio', command: 'new', args: ['--new'], env: { TOKEN: 'secret', REGION: 'us' } });
    expect(connectorNeedsUpdate(merged, def)).toBe(false);
  });

  it('keeps user-added extra env keys', () => {
    const merged = mergeConnectorUpdate(
      { type: 'stdio', command: 'x', env: { CUSTOM: 'mine' } },
      { type: 'stdio', command: 'x' }
    );
    expect(merged.env).toEqual({ CUSTOM: 'mine' });
  });
});

describe('collectDriftedItems', () => {
  it('keeps only installed connectors that drifted, deduped by identity across repos', () => {
    const drifted = buildExplorePlugins(
      'omni/official',
      manifest({
        connectors: [
          { id: 'github', label: 'GitHub', description: 'PRs', server: { type: 'stdio', command: 'gh-mcp-v2' } },
          { id: 'teams', label: 'Teams', description: 'Chats', server: { type: 'http', url: 'https://mcp' } },
        ],
        plugins: [{ name: 'dev-tools', description: 'Dev', source: '.', skills: [] }],
      }),
      {
        mcpConfig: mcpConfig({ github: { type: 'stdio', command: 'gh-mcp' } }),
        skills: [skill()],
        updates: { [bundleUpdateKey('omni/official', 'dev-tools')]: update() },
      }
    );
    // Same catalog surfaced by a second marketplace — must not double-count.
    const fromSecondRepo = drifted.map((p) => ({ ...p, repo: 'other/mirror' }));

    const result = collectDriftedItems([...drifted, ...fromSecondRepo]);
    expect(result).toHaveLength(1);
    expect(result).toMatchObject([{ kind: 'connector', repo: 'omni/official', connector: { id: 'github' } }]);
  });

  it('returns empty when nothing drifted', () => {
    expect(collectDriftedItems([])).toEqual([]);
  });
});

describe('buildExplorePlugins', () => {
  const ctx = {
    mcpConfig: mcpConfig({ github: { type: 'stdio', command: 'gh-mcp' } }),
    skills: [skill()],
    updates: { [bundleUpdateKey('omni/official', 'dev-tools')]: update() },
    customApps: [{ id: 'a1', label: 'Teams', icon: 'Globe', url: 'https://teams', order: 50 }],
  };

  it('detects connector installs by mcpServers key', () => {
    const items = buildExplorePlugins(
      'omni/official',
      manifest({
        connectors: [
          { id: 'github', label: 'GitHub', description: 'PRs', server: { type: 'stdio', command: 'gh-mcp' } },
          { id: 'teams', label: 'Teams', description: 'Chats', server: { type: 'http', url: 'https://mcp' } },
        ],
      }),
      ctx
    );
    expect(items).toMatchObject([
      { kind: 'connector', installed: true, needsUpdate: false },
      { kind: 'connector', installed: false, needsUpdate: false },
    ]);
  });

  it('flags an installed connector whose definition drifted', () => {
    const items = buildExplorePlugins(
      'omni/official',
      manifest({
        connectors: [
          { id: 'github', label: 'GitHub', description: 'PRs', server: { type: 'stdio', command: 'gh-mcp-v2' } },
        ],
      }),
      ctx
    );
    expect(items).toMatchObject([{ kind: 'connector', installed: true, needsUpdate: true }]);
  });

  it('detects bundle installs and carries their update report', () => {
    const items = buildExplorePlugins(
      'omni/official',
      manifest({
        plugins: [
          { name: 'dev-tools', description: 'Dev', source: '.', skills: ['git-workflow'] },
          { name: 'writing', description: 'Docs', source: '.', skills: ['memo'] },
        ],
      }),
      ctx
    );
    expect(items).toMatchObject([
      { kind: 'skill', installed: true, update: { plugin: 'dev-tools' } },
      { kind: 'skill', installed: false, update: undefined },
    ]);
  });
});

describe('filterPlugins', () => {
  const items = buildInstalledPlugins({
    mcpConfig: mcpConfig({ github: { type: 'stdio', command: 'gh-mcp' } }),
    skills: [skill()],
    updates: {},
  });

  it('passes everything through for all + empty query', () => {
    expect(filterPlugins(items, 'all', '')).toHaveLength(2);
  });

  it('filters by kind', () => {
    expect(filterPlugins(items, 'connector', '').map(displayName)).toEqual(['github']);
    expect(filterPlugins(items, 'skill', '').map(displayName)).toEqual(['git-workflow']);
  });

  it('matches queries case-insensitively on name and description', () => {
    expect(filterPlugins(items, 'all', 'GIT HELPERS').map(displayName)).toEqual(['git-workflow']);
    expect(filterPlugins(items, 'all', 'zoom').map(displayName)).toEqual([]);
    expect(filterPlugins(items, 'all', 'git').map(displayName)).toEqual(['github', 'git-workflow']);
    expect(filterPlugins(items, 'all', 'workflow').map(displayName)).toEqual(['git-workflow']);
  });

  it('matches explore skill bundles by their title-cased display name', () => {
    const explore = buildExplorePlugins(
      'omni/official',
      manifest({ plugins: [{ name: 'dev-tools', description: 'Dev', source: '.', skills: [] }] }),
      { mcpConfig: null, skills: [], updates: {} }
    );
    expect(filterPlugins(explore, 'skill', 'Dev Tools')).toHaveLength(1);
    expect(filterPlugins(explore, 'skill', 'dev-tools')).toHaveLength(1);
  });

  it('combines kind and query', () => {
    expect(filterPlugins(items, 'skill', 'github')).toHaveLength(0);
    expect(filterPlugins(items, 'connector', 'github')).toHaveLength(1);
  });
});
