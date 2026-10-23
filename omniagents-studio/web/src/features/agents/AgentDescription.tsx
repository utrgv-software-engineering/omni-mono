import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'

import { api } from '../../lib/api'
import type { AgentDescription as Description } from '../../types'

const approvalLabel: Record<Description['tools'][number]['approval'], string> = {
  always: 'asks first',
  conditional: 'decided per call',
  never: 'runs without asking',
}

const approvalsLabel: Record<Description['policy']['approvals'], string> = {
  enforced: 'Enforced by the project: every tool not marked safe asks first',
  safe_agent: 'Safe agent: tools not marked safe ask first',
  none: 'Tools run without asking',
}

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="rounded-md border border-border bg-background">
      <h3 className="flex items-center gap-2 border-b border-border px-4 py-2 text-sm font-semibold">
        {title}
        {count !== undefined ? <span className="text-xs font-normal text-muted-foreground">{count}</span> : null}
      </h3>
      <div className="px-4 py-3 text-sm">{children}</div>
    </section>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>
}

function NameList({ items }: { items: Array<{ name: string; description?: string; error?: string }> }) {
  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <li key={item.name}>
          <span className="font-mono text-xs">{item.name}</span>
          {item.error ? (
            <span className="ml-2 text-xs text-destructive">{item.error}</span>
          ) : item.description ? (
            <span className="ml-2 text-xs text-muted-foreground">{item.description}</span>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

/**
 * What a run of the agent is given, as its runtime resolves it: the built
 * agent's model, tools and rendered instructions, with its subagents,
 * capabilities, skills and approval policy. Read-only; the agent's files
 * are the place to change it.
 */
export function AgentDescription({ agentKey }: { agentKey: string }) {
  const { data, isLoading, error } = useQuery<Description, Error>({
    queryKey: ['agents', agentKey, 'description'],
    queryFn: () => api.describeAgent(agentKey),
    staleTime: 60 * 1000,
  })

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Resolving the agent…
      </div>
    )
  }
  if (error || !data) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
        {error?.message ?? 'The agent could not be resolved.'}
      </div>
    )
  }

  const settings = Object.entries(data.model.settings)
  const { policy } = data
  return (
    <div className="flex flex-col gap-4">
      <Section title="Model">
        <div className="font-mono text-sm">{data.model.name ?? 'Chosen by the runtime at run time'}</div>
        {settings.length > 0 ? (
          <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
            {settings.map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-muted-foreground">{key}</dt>
                <dd className="font-mono break-all">{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </Section>

      <Section title="Tools" count={data.tools.length}>
        {data.tools.length === 0 ? (
          <Empty>No tools.</Empty>
        ) : (
          <ul className="divide-y divide-border">
            {data.tools.map((tool) => (
              <li key={tool.name} className="py-2">
                <details>
                  <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-2">
                    <span className="font-mono text-xs font-medium">{tool.name}</span>
                    {tool.kind !== 'function' ? (
                      <span className="rounded-sm bg-muted px-1.5 text-[11px] text-muted-foreground">{tool.kind}</span>
                    ) : null}
                    <span className="text-[11px] text-muted-foreground">{approvalLabel[tool.approval]}</span>
                  </summary>
                  {tool.description ? (
                    <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">{tool.description}</p>
                  ) : null}
                  {tool.parameters ? (
                    <pre className="mt-2 max-h-64 overflow-auto rounded-sm bg-muted p-2 text-[11px]">
                      {JSON.stringify(tool.parameters, null, 2)}
                    </pre>
                  ) : null}
                </details>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {data.agent_tools.length > 0 || data.handoffs.length > 0 ? (
        <Section title="Subagents" count={data.agent_tools.length + data.handoffs.length}>
          <ul className="space-y-1.5">
            {data.agent_tools.map((tool) => (
              <li key={tool.name}>
                <span className="font-mono text-xs">{tool.name}</span>
                <span className="ml-2 text-xs text-muted-foreground">
                  runs {tool.agent} as a tool{tool.max_turns ? `, up to ${tool.max_turns} turns` : ''}
                  {tool.description ? ` — ${tool.description}` : ''}
                </span>
              </li>
            ))}
            {data.handoffs.map((handoff) => (
              <li key={handoff.name}>
                <span className="font-mono text-xs">{handoff.name}</span>
                <span className="ml-2 text-xs text-muted-foreground">
                  hands off{handoff.description ? ` — ${handoff.description}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="Approvals and limits">
        <ul className="space-y-1 text-xs">
          <li>{approvalsLabel[policy.approvals]}</li>
          {policy.skip_approvals ? <li>Approvals are skipped for this agent</li> : null}
          {policy.safe_tool_names.length > 0 ? (
            <li>
              Safe tools: <span className="font-mono">{policy.safe_tool_names.join(', ')}</span>
            </li>
          ) : null}
          {policy.safe_tool_patterns.length > 0 ? (
            <li>
              Safe patterns: <span className="font-mono">{policy.safe_tool_patterns.join(', ')}</span>
            </li>
          ) : null}
          {policy.max_turns ? <li>Up to {policy.max_turns} turns per run</li> : null}
          {policy.compaction ? (
            <li>
              Compaction: <span className="font-mono">{JSON.stringify(policy.compaction)}</span>
            </li>
          ) : null}
        </ul>
      </Section>

      <Section title="Capabilities" count={data.capabilities.length}>
        {data.capabilities.length === 0 ? (
          <Empty>No capability packs.</Empty>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {data.capabilities.map((name) => (
              <span key={name} className="rounded-sm bg-muted px-2 py-0.5 font-mono text-xs">
                {name}
              </span>
            ))}
          </div>
        )}
      </Section>

      {data.mcp_servers.length > 0 ? (
        <Section title="MCP servers" count={data.mcp_servers.length}>
          <NameList items={data.mcp_servers.map((server) => ({ name: server.name, description: server.type }))} />
        </Section>
      ) : null}

      {data.skills.length > 0 ? (
        <Section title="Skills" count={data.skills.length}>
          <NameList items={data.skills} />
        </Section>
      ) : null}

      <Section title="Commands and server functions" count={data.server_functions.length}>
        {data.server_functions.length === 0 ? <Empty>None.</Empty> : <NameList items={data.server_functions} />}
      </Section>

      <Section title="Instructions">
        {data.instructions.error ? (
          <p className="text-xs text-destructive">
            These instructions are rendered when a run starts and could not be rendered here: {data.instructions.error}
          </p>
        ) : (
          <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap text-xs leading-relaxed">
            {data.instructions.text || '(none)'}
          </pre>
        )}
      </Section>
    </div>
  )
}
