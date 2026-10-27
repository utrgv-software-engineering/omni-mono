import { Badge } from '@omniagents/ui/badge'
import { Button } from '@omniagents/ui/button'
import { Input } from '@omniagents/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@omniagents/ui/tabs'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronRight, MessageSquare, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { api } from '../../lib/api'
import type { AgentSummary } from '../../types'
import { AgentDescription } from './AgentDescription'
import { AgentEditor } from './AgentEditor'

interface AgentsViewProps {
  selectedAgent: string | null
  onSelectAgent: (agentKey: string) => void
  onClearAgent: () => void
  /** Open the agent in the Playground. */
  onChat: (agentKey: string) => void
}

/** The project's agents: what each runs with, and editing it. */
export function AgentsView({ selectedAgent, onSelectAgent, onClearAgent, onChat }: AgentsViewProps) {
  const { data, isLoading, error } = useQuery<{ agents: AgentSummary[] }>({
    queryKey: ['agents', 'list'],
    queryFn: () => api.getAgents(),
  })
  const agents = data?.agents ?? []

  if (selectedAgent) {
    const agent = agents.find((a) => a.key === selectedAgent)
    return <AgentDetail agentKey={selectedAgent} agent={agent ?? null} onChat={onChat} onDeleted={onClearAgent} />
  }

  return (
    <div className="h-full w-full overflow-y-auto p-6">
      <div className="mx-auto flex max-w-6xl flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold">Agents</h2>
          <p className="text-sm text-muted-foreground">
            What each agent runs with, as its runtime resolves it, and its settings and instructions to edit.
          </p>
        </div>
        {isLoading ? <p className="text-sm text-muted-foreground">Loading agents…</p> : null}
        {error ? <p className="text-sm text-destructive">{(error as Error).message}</p> : null}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {agents.map((agent) => (
            <button
              key={agent.key}
              type="button"
              onClick={() => onSelectAgent(agent.key)}
              className="flex h-full flex-col rounded-md border border-border bg-background p-4 text-left transition-shadow hover:shadow-sm"
            >
              <div className="mb-2 flex items-start justify-between gap-3">
                <div className="flex items-center gap-2">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <Bot className="h-4 w-4" />
                  </div>
                  <div className="space-y-1">
                    <div className="text-sm font-semibold">{agent.label}</div>
                    {agent.is_entry ? <Badge variant="secondary">Entrypoint</Badge> : null}
                  </div>
                </div>
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              </div>
              {agent.error ? (
                <p className="text-xs text-destructive">Does not load: {agent.error}</p>
              ) : agent.description ? (
                <p className="text-xs text-muted-foreground">{agent.description}</p>
              ) : null}
            </button>
          ))}
          <NewAgent onCreated={onSelectAgent} />
        </div>
      </div>
    </div>
  )
}

function NewAgent({ onCreated }: { onCreated: (key: string) => void }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState('')
  const [model, setModel] = useState('gpt-4.1')
  const create = useMutation({
    mutationFn: () => api.createAgent({ key: key.trim(), model: model.trim() }),
    onSuccess: (source) => {
      void queryClient.invalidateQueries({ queryKey: ['agents'] })
      onCreated(source.key)
    },
  })
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex min-h-24 items-center justify-center gap-2 rounded-md border border-dashed border-border text-sm text-muted-foreground hover:bg-muted"
      >
        <Plus className="h-4 w-4" /> New agent
      </button>
    )
  }
  return (
    <div className="space-y-2 rounded-md border border-border p-4">
      <Input aria-label="Agent key" placeholder="agent_key (its folder under agents/)" value={key} onChange={(e) => setKey(e.target.value)} />
      <Input aria-label="Model" value={model} onChange={(e) => setModel(e.target.value)} />
      <div className="flex gap-2">
        <Button size="sm" disabled={!key.trim() || !model.trim() || create.isPending} onClick={() => create.mutate()}>
          Create
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {create.error ? <p className="whitespace-pre-wrap text-xs text-destructive">{(create.error as Error).message}</p> : null}
    </div>
  )
}

function AgentDetail({
  agentKey,
  agent,
  onChat,
  onDeleted,
}: {
  agentKey: string
  agent: AgentSummary | null
  onChat: (agentKey: string) => void
  onDeleted: () => void
}) {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState(agent?.error ? 'edit' : 'runs')
  const remove = useMutation({
    mutationFn: () => api.deleteAgent(agentKey),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['agents'] })
      onDeleted()
    },
  })
  return (
    <div className="h-full w-full overflow-y-auto p-6">
      <div className="mx-auto flex max-w-5xl flex-col gap-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">{agent?.label ?? agentKey}</h2>
            {agent?.description ? <p className="text-sm text-muted-foreground">{agent.description}</p> : null}
            {agent?.error ? <p className="text-sm text-destructive">Does not load: {agent.error}</p> : null}
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={Boolean(agent?.error)} onClick={() => onChat(agentKey)}>
              <MessageSquare /> Chat
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => window.confirm(`Delete ${agent?.label ?? agentKey} and its folder?`) && remove.mutate()}
            >
              <Trash2 /> Delete
            </Button>
          </div>
        </div>
        {remove.error ? <p className="text-sm text-destructive">{(remove.error as Error).message}</p> : null}
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="runs" disabled={Boolean(agent?.error)}>
              What it runs with
            </TabsTrigger>
            <TabsTrigger value="edit">Edit</TabsTrigger>
          </TabsList>
          <TabsContent value="runs">
            <AgentDescription agentKey={agentKey} />
          </TabsContent>
          <TabsContent value="edit">
            <AgentEditor agentKey={agentKey} onSaved={() => setTab(agent?.error ? 'edit' : 'runs')} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
