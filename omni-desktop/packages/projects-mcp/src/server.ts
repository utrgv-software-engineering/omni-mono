import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { IProjectsRepo } from 'omni-projects-db';

import { registerCommentTools } from './tools/comments.js';
import { registerHandbookTools } from './tools/handbook.js';
import { registerInboxTools } from './tools/inbox.js';
import { registerMilestoneTools } from './tools/milestones.js';
import { registerPageTools } from './tools/pages.js';
import { registerPipelineTools } from './tools/pipeline.js';
import { registerProjectTools } from './tools/projects.js';
import { registerTicketTools } from './tools/tickets.js';

export interface ProjectsMcpContext {
  listSandboxProfiles?: () => Promise<Array<{ name: string; label?: string; available?: boolean; source?: string }>>;
  getCurrentPrincipal?: () => Promise<string | null>;
}

/**
 * Build the omni-projects MCP server over an {@link IProjectsRepo} (the
 * stdio `cli.ts` supplies a `SqliteProjectsRepo`).
 *
 * Page bodies live in the DB (`getPageContent`/`setPageContent`), so the
 * server no longer touches the filesystem.
 */
export function createServer(repo: IProjectsRepo, context: ProjectsMcpContext = {}): McpServer {
  const server = new McpServer({ name: 'omni-projects', version: '0.1.0' }, { capabilities: { tools: {} } });

  registerProjectTools(server, repo);
  registerTicketTools(server, repo);
  registerCommentTools(server, repo, context);
  registerMilestoneTools(server, repo);
  registerPageTools(server, repo);
  registerInboxTools(server, repo);
  registerPipelineTools(server, repo);
  registerHandbookTools(server, repo, context);

  server.tool(
    'list_sandbox_profiles',
    'List sandbox profile names valid for project sandbox_profile and launch profile settings.',
    {},
    async () => {
      const profiles = context.listSandboxProfiles
        ? await context.listSandboxProfiles()
        : [
            { name: 'host', label: 'This computer (no sandbox)', available: true, source: 'builtin' },
            { name: 'devbox', label: 'Devbox (Docker)', available: true, source: 'builtin' },
          ];
      return { content: [{ type: 'text' as const, text: JSON.stringify({ profiles }) }] };
    }
  );

  server.tool(
    'list_team_members',
    'List assignable principals: the resident agents (user_id "agent:<id>", role "agent").',
    {},
    async () => {
      const members = (await repo.listResidents())
        .filter((r) => r.enabled === 1)
        .map((r) => ({ user_id: `agent:${r.id}`, display_name: r.name, role: 'agent' }));
      return { content: [{ type: 'text' as const, text: JSON.stringify({ members }) }] };
    }
  );

  server.tool(
    'get_current_principal',
    'Return the current principal ID for assigning tickets to self — `agent:<id>` in a resident agent session, ' +
      'null in a user session.',
    {},
    async () => {
      const principal = context.getCurrentPrincipal ? await context.getCurrentPrincipal() : null;
      return { content: [{ type: 'text' as const, text: JSON.stringify({ principal }) }] };
    }
  );

  return server;
}
