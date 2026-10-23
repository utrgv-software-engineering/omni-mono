import { Button } from '@omniagents/ui/button'
import { Checkbox } from '@omniagents/ui/checkbox'
import { Input } from '@omniagents/ui/input'
import { Label } from '@omniagents/ui/label'
import { Switch } from '@omniagents/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@omniagents/ui/tabs'
import { Textarea } from '@omniagents/ui/textarea'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'

import { ApiError, api } from '../../lib/api'
import type { AgentOptions } from '../../types/evaluation'
import { changesOf, type Draft, draftOf } from './agentDraft'

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-md border border-border p-4">
      <div>
        <h4 className="text-sm font-semibold">{title}</h4>
        {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      </div>
      {children}
    </section>
  )
}

function Picks({ options, chosen, onChange, mono = true }: { options: string[]; chosen: string[]; onChange: (next: string[]) => void; mono?: boolean }) {
  if (options.length === 0) return <p className="text-xs text-muted-foreground">None in this project.</p>
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-2">
      {options.map((option) => (
        <label key={option} className="flex items-center gap-2 text-xs">
          <Checkbox
            checked={chosen.includes(option)}
            onCheckedChange={(checked) => onChange(checked ? [...chosen, option] : chosen.filter((c) => c !== option))}
          />
          <span className={mono ? 'font-mono' : undefined}>{option}</span>
        </label>
      ))}
    </div>
  )
}

/**
 * Editing an agent's files. Saving changes only the settings edited here
 * (comments and everything else in agent.yml stay), and is refused, with
 * each problem, when the agent would not load with what it names.
 */
export function AgentEditor({ agentKey, onSaved }: { agentKey: string; onSaved: () => void }) {
  const queryClient = useQueryClient()
  const source = useQuery({ queryKey: ['agents', agentKey, 'source'], queryFn: () => api.agentSource(agentKey) })
  const options = useQuery<AgentOptions>({ queryKey: ['agents', 'options'], queryFn: () => api.agentOptions() })
  const original = useMemo(() => (source.data ? draftOf(source.data) : null), [source.data])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [yaml, setYaml] = useState('')
  const [problems, setProblems] = useState<string[]>([])
  useEffect(() => {
    setDraft(original)
    setYaml(source.data?.yaml ?? '')
  }, [original, source.data])

  const saved = () => {
    setProblems([])
    void queryClient.invalidateQueries({ queryKey: ['agents'] })
    onSaved()
  }
  const refused = (error: Error) => setProblems(error instanceof ApiError && error.problems.length ? error.problems : [error.message])
  const saveForm = useMutation({
    mutationFn: async () => {
      const { changes, problems: local } = changesOf(draft!, original!, source.data?.settings ?? {})
      if (local.length) throw new ApiError(422, local.join('\n'), local)
      return api.editAgent(agentKey, {
        changes,
        instructions: draft!.instructions !== original!.instructions ? draft!.instructions : null,
      })
    },
    onSuccess: saved,
    onError: refused,
  })
  const saveYaml = useMutation({ mutationFn: () => api.replaceAgentYaml(agentKey, yaml), onSuccess: saved, onError: refused })

  if (source.isLoading || !source.data) return <p className="text-sm text-muted-foreground">Loading the agent&apos;s files…</p>
  const set = (change: Partial<Draft>) => setDraft((current) => (current ? { ...current, ...change } : current))
  const capabilities = options.data?.capabilities ?? []
  const guardrails = options.data?.guardrails ?? { input: [], output: [], tool_input: [], tool_output: [] }
  const formChanged = draft && original && JSON.stringify(draft) !== JSON.stringify(original)

  return (
    <div className="space-y-4">
      <p className="font-mono text-xs text-muted-foreground">{source.data.path}</p>
      {problems.length ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <p className="font-medium">Not saved:</p>
          <ul className="list-disc pl-5">
            {problems.map((problem) => (
              <li key={problem} className="whitespace-pre-wrap">
                {problem}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <Tabs defaultValue={source.data.error ? 'yaml' : 'form'}>
        <TabsList>
          <TabsTrigger value="form" disabled={!draft}>
            Settings
          </TabsTrigger>
          <TabsTrigger value="yaml">agent.yml</TabsTrigger>
        </TabsList>
        <TabsContent value="form" className="space-y-4">
          {draft ? (
            <>
              <div className="flex justify-end">
                <Button size="sm" disabled={!formChanged || saveForm.isPending} onClick={() => saveForm.mutate()}>
                  {saveForm.isPending ? 'Saving…' : 'Save'}
                </Button>
              </div>
              <Section title="Agent">
                <div className="grid grid-cols-2 gap-3">
                  <div className="grid gap-1.5">
                    <Label>Label</Label>
                    <Input value={draft.label} onChange={(e) => set({ label: e.target.value })} />
                  </div>
                  <div className="grid gap-1.5">
                    <Label>Model</Label>
                    <Input value={draft.model} placeholder="The runtime's default" onChange={(e) => set({ model: e.target.value })} />
                  </div>
                  <div className="grid gap-1.5">
                    <Label>Description</Label>
                    <Input value={draft.description} onChange={(e) => set({ description: e.target.value })} />
                  </div>
                  <div className="grid gap-1.5">
                    <Label>Turn limit</Label>
                    <Input type="number" min={1} value={draft.max_turns} onChange={(e) => set({ max_turns: e.target.value })} />
                  </div>
                </div>
              </Section>
              <Section
                title="Instructions"
                hint={
                  source.data.instructions_path
                    ? `Saved in ${source.data.instructions_path}. A Jinja template: {{ workspace_root }} and {% include %} work.`
                    : 'Inline in agent.yml. A Jinja template.'
                }
              >
                <Textarea rows={14} className="font-mono text-xs" value={draft.instructions} onChange={(e) => set({ instructions: e.target.value })} />
              </Section>
              <Section title="Capabilities" hint="Packs of tools and instructions. The project's own and the builtin ones.">
                <div className="grid gap-2 sm:grid-cols-2">
                  {capabilities.map((capability) => (
                    <label key={capability.name} className="flex items-start gap-2 text-xs" title={capability.tools.join(', ')}>
                      <Checkbox
                        checked={draft.capabilities.includes(capability.name)}
                        onCheckedChange={(checked) =>
                          set({
                            capabilities: checked
                              ? [...draft.capabilities, capability.name]
                              : draft.capabilities.filter((c) => c !== capability.name),
                          })
                        }
                      />
                      <span>
                        <span className="font-mono">{capability.name}</span>
                        <span className="block text-muted-foreground">{capability.tools.slice(0, 6).join(', ')}</span>
                      </span>
                    </label>
                  ))}
                </div>
              </Section>
              <Section title="Tools" hint="Single tools from the project's tools directories, beyond what capabilities give.">
                <Picks options={options.data?.tools ?? []} chosen={draft.tools} onChange={(tools) => set({ tools })} />
              </Section>
              <Section title="Approvals" hint="With the safe agent on, tools not marked safe ask before running.">
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={draft.use_safe_agent} onCheckedChange={(checked) => set({ use_safe_agent: checked })} />
                  Ask before running tools
                </label>
                {draft.use_safe_agent ? (
                  <>
                    <p className="text-xs font-medium">Safe tools (run without asking)</p>
                    <Picks
                      options={[...new Set([...(options.data?.tools ?? []), ...capabilities.flatMap((c) => (draft.capabilities.includes(c.name) ? c.tools : []))])].sort()}
                      chosen={draft.safe_tool_names}
                      onChange={(safe_tool_names) => set({ safe_tool_names })}
                    />
                    <label className="flex items-center gap-2 text-sm">
                      <Switch checked={draft.halt_on_rejection} onCheckedChange={(checked) => set({ halt_on_rejection: checked })} />
                      Stop the run when a tool is rejected
                    </label>
                  </>
                ) : null}
              </Section>
              <Section title="Guardrails" hint="Checks on the user's input and the final output, from the project's guardrails directory.">
                <p className="text-xs font-medium">Input</p>
                <Picks options={guardrails.input} chosen={draft.input_guardrails} onChange={(input_guardrails) => set({ input_guardrails })} />
                <p className="text-xs font-medium">Output</p>
                <Picks options={guardrails.output} chosen={draft.output_guardrails} onChange={(output_guardrails) => set({ output_guardrails })} />
              </Section>
              <Section title="Model settings" hint='JSON, e.g. {"temperature": 0.2, "parallel_tool_calls": true}.'>
                <Textarea rows={4} className="font-mono text-xs" value={draft.model_settings} onChange={(e) => set({ model_settings: e.target.value })} />
              </Section>
              <Section title="Structured output" hint="A JSON schema the run must end with. Empty for a text reply.">
                <Textarea rows={6} className="font-mono text-xs" value={draft.output_schema} onChange={(e) => set({ output_schema: e.target.value })} />
                <label className="flex items-center gap-2 text-sm">
                  <Switch checked={draft.output_type_strict} onCheckedChange={(checked) => set({ output_type_strict: checked })} />
                  Strict schema
                </label>
              </Section>
              <p className="text-xs text-muted-foreground">
                Handoffs, subagents, MCP servers and everything else are edited in agent.yml.
              </p>
            </>
          ) : null}
        </TabsContent>
        <TabsContent value="yaml" className="space-y-3">
          {source.data.error ? <p className="text-sm text-destructive">{source.data.error}</p> : null}
          <Textarea rows={28} className="font-mono text-xs" value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} />
          <div className="flex justify-end">
            <Button size="sm" disabled={yaml === source.data.yaml || saveYaml.isPending} onClick={() => saveYaml.mutate()}>
              {saveYaml.isPending ? 'Saving…' : 'Save agent.yml'}
            </Button>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
