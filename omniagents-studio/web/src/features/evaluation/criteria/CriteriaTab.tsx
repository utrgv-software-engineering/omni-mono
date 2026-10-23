import { Badge } from '@omniagents/ui/badge'
import { Button } from '@omniagents/ui/button'
import { Checkbox } from '@omniagents/ui/checkbox'
import { Input } from '@omniagents/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@omniagents/ui/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Pencil, Search, Sparkles, Trash2, X } from 'lucide-react'
import { useState } from 'react'

import { api } from '../../../lib/api'
import type { FailureCategory } from '../../../types'
import type { FailureModeProposal, Measure } from '../../../types/evaluation'
import { JobPanel } from '../jobs'
import { useJob } from '../useJob'

/**
 * What the evaluation looks for: failure categories (the ways the agent
 * goes wrong, with the measures that catch each) and the measures
 * themselves.
 */
export function CriteriaTab() {
  return (
    <div className="space-y-10 p-6">
      <Categories />
      <FailureModesFromNotes />
      <Measures />
    </div>
  )
}

function Categories() {
  const queryClient = useQueryClient()
  const categories = useQuery({ queryKey: ['evaluation', 'categories'], queryFn: () => api.getCategories() })
  const measures = useQuery({ queryKey: ['evaluation', 'measures'], queryFn: () => api.getMeasures() })
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [editing, setEditing] = useState<FailureCategory | null>(null)
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['evaluation'] })
  const mutate = useMutation({ mutationFn: (action: () => Promise<unknown>) => action(), onSuccess: refresh })
  const assist = useQuery({ queryKey: ['evaluation', 'review-assist'], queryFn: () => api.getReviewAssist() })
  const scan = useJob([['evaluation', 'review-assist']])
  const pendingFor = (id: string) =>
    (assist.data?.suggestions ?? []).filter((s) => s.category === id && s.status === 'pending').length

  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-base font-semibold">Failure categories</h3>
        <p className="text-xs text-muted-foreground">
          The ways the agent goes wrong. Scenarios name the categories they probe; reviewers mark conversations with the
          ones they see; each category&apos;s measures decide whether a run shows it.
        </p>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Category</TableHead>
            <TableHead>Measures</TableHead>
            <TableHead>Scenarios</TableHead>
            <TableHead>Reviews</TableHead>
            <TableHead>Suggested</TableHead>
            <TableHead className="w-20" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {(categories.data?.categories ?? []).map((category) =>
            editing?.id === category.id ? (
              <CategoryEditor
                key={category.id}
                category={category}
                measures={measures.data?.measures ?? []}
                onDone={() => {
                  setEditing(null)
                  refresh()
                }}
              />
            ) : (
              <TableRow key={category.id}>
                <TableCell>
                  <div className="font-medium">{category.name}</div>
                  {category.description ? <div className="text-xs text-muted-foreground">{category.description}</div> : null}
                </TableCell>
                <TableCell className="font-mono text-xs">{category.measures.join(', ') || '—'}</TableCell>
                <TableCell>{category.scenario_count}</TableCell>
                <TableCell>{category.review_count}</TableCell>
                <TableCell className="text-xs">
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={scan.running}
                    title="Read every evaluation answer for this failure and suggest the ones that may show it"
                    onClick={() => void scan.start({ kind: 'suggest_labels', category: category.id })}
                  >
                    <Search /> Find more
                  </Button>
                  {pendingFor(category.id) ? (
                    <span className="ml-2 text-muted-foreground">{pendingFor(category.id)} to review in Logs</span>
                  ) : null}
                </TableCell>
                <TableCell>
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="icon-xs" aria-label="Edit" onClick={() => setEditing(category)}>
                      <Pencil />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label="Delete"
                      onClick={() => {
                        if (
                          window.confirm(
                            `Delete ${category.name}? It is removed from ${category.scenario_count} scenarios and ${category.review_count} reviews.`,
                          )
                        ) {
                          mutate.mutate(() => api.removeCategory(category.id))
                        }
                      }}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ),
          )}
        </TableBody>
      </Table>
      <div className="flex gap-2">
        <Input placeholder="New category" value={name} onChange={(e) => setName(e.target.value)} className="max-w-xs" />
        <Input placeholder="What it means" value={description} onChange={(e) => setDescription(e.target.value)} />
        <Button
          size="sm"
          disabled={!name.trim() || mutate.isPending}
          onClick={() =>
            mutate.mutate(async () => {
              await api.addCategory({ name: name.trim(), description: description.trim() || null })
              setName('')
              setDescription('')
            })
          }
        >
          Add
        </Button>
      </div>
      {mutate.error ? <p className="text-sm text-destructive">{(mutate.error as Error).message}</p> : null}
      <JobPanel job={scan.job} error={scan.error} onDismiss={scan.dismiss} />
    </section>
  )
}

/**
 * The reviewer notices, the assistant organizes: review notes grouped into
 * failure modes, matched to existing categories or proposed as new ones.
 * Accepting one labels the reviews it grouped; nothing is labelled until
 * then.
 */
function FailureModesFromNotes() {
  const queryClient = useQueryClient()
  const assist = useQuery({ queryKey: ['evaluation', 'review-assist'], queryFn: () => api.getReviewAssist() })
  const categories = useQuery({ queryKey: ['evaluation', 'categories'], queryFn: () => api.getCategories() })
  const propose = useJob([['evaluation', 'review-assist']])
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['evaluation'] })
  const proposals = assist.data?.proposals ?? []
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold">Failure modes from review notes</h3>
          <p className="text-xs text-muted-foreground">
            The review assistant groups reviewers&apos; notes by the failure they describe, using existing categories where
            they fit and proposing new ones where none does. Accept a mode to label the reviews it grouped.
          </p>
        </div>
        <Button size="sm" variant="outline" disabled={propose.running} onClick={() => void propose.start({ kind: 'propose_modes' })}>
          <Sparkles /> Propose from notes
        </Button>
      </div>
      <JobPanel job={propose.job} error={propose.error} onDismiss={propose.dismiss} />
      {proposals.length === 0 ? <p className="text-sm text-muted-foreground">No proposals pending.</p> : null}
      {proposals.map((proposal) => (
        <Proposal
          key={proposal.id}
          proposal={proposal}
          existingName={
            proposal.existing_category
              ? (categories.data?.categories.find((c) => c.id === proposal.existing_category)?.name ?? proposal.existing_category)
              : null
          }
          onDone={refresh}
        />
      ))}
    </section>
  )
}

function Proposal({
  proposal,
  existingName,
  onDone,
}: {
  proposal: FailureModeProposal
  existingName: string | null
  onDone: () => void
}) {
  const [name, setName] = useState(proposal.name)
  const [description, setDescription] = useState(proposal.description ?? '')
  const act = useMutation({ mutationFn: (action: () => Promise<unknown>) => action(), onSuccess: onDone })
  return (
    <div className="space-y-2 rounded-md border border-dashed border-violet-400/60 p-3">
      {existingName ? (
        <p className="text-sm">
          <span className="font-medium">{existingName}</span>{' '}
          <span className="text-xs text-muted-foreground">(existing category)</span>
        </p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-[16rem_1fr]">
          <Input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <Input aria-label="What it means" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
      )}
      <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
        {proposal.notes.slice(0, 5).map((note, index) => (
          <li key={index}>{note}</li>
        ))}
        {proposal.notes.length > 5 ? <li>and {proposal.notes.length - 5} more</li> : null}
      </ul>
      <div className="flex gap-2">
        <Button
          size="xs"
          disabled={act.isPending || (!existingName && !name.trim())}
          onClick={() =>
            act.mutate(() =>
              api.acceptProposal(proposal.id, existingName ? {} : { name: name.trim(), description: description.trim() || null }),
            )
          }
        >
          <Check /> Accept and label {proposal.review_ids.length} reviews
        </Button>
        <Button size="xs" variant="ghost" disabled={act.isPending} onClick={() => act.mutate(() => api.dismissProposal(proposal.id))}>
          <X /> Dismiss
        </Button>
      </div>
      {act.error ? <p className="text-xs text-destructive">{(act.error as Error).message}</p> : null}
    </div>
  )
}

function CategoryEditor({ category, measures, onDone }: { category: FailureCategory; measures: Measure[]; onDone: () => void }) {
  const [description, setDescription] = useState(category.description ?? '')
  const [chosen, setChosen] = useState<string[]>(category.measures)
  const save = useMutation({
    mutationFn: () => api.updateCategory(category.id, { description, measures: chosen }),
    onSuccess: onDone,
  })
  return (
    <TableRow>
      <TableCell colSpan={5} className="space-y-3 bg-muted/30">
        <div className="font-medium">{category.name}</div>
        <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What it means" />
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {measures.map((measure) => (
            <label key={measure.name} className="flex items-center gap-2 text-xs">
              <Checkbox
                checked={chosen.includes(measure.name)}
                onCheckedChange={(checked) =>
                  setChosen((current) => (checked ? [...current, measure.name] : current.filter((m) => m !== measure.name)))
                }
              />
              <span className="font-mono">{measure.name}</span>
            </label>
          ))}
        </div>
        <div className="flex gap-2">
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
            Save
          </Button>
          <Button size="sm" variant="ghost" onClick={onDone}>
            Cancel
          </Button>
          {save.error ? <span className="text-xs text-destructive">{(save.error as Error).message}</span> : null}
        </div>
      </TableCell>
    </TableRow>
  )
}

function Measures() {
  const queryClient = useQueryClient()
  const measures = useQuery({ queryKey: ['evaluation', 'measures'], queryFn: () => api.getMeasures() })
  const [name, setName] = useState('')
  const [label, setLabel] = useState('')
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['evaluation', 'measures'] })
  const mutate = useMutation({ mutationFn: (action: () => Promise<unknown>) => action(), onSuccess: refresh })

  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-base font-semibold">Measures</h3>
        <p className="text-xs text-muted-foreground">
          Functions that score a run: the built-in ones and those in the project&apos;s measures.py. A new measure gets a
          function there that fails until you write it.
        </p>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Measure</TableHead>
            <TableHead>From</TableHead>
            <TableHead>Used by</TableHead>
            <TableHead className="w-20" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {(measures.data?.measures ?? []).map((measure) => (
            <TableRow key={measure.name}>
              <TableCell>
                <div className="font-mono text-xs">{measure.name}</div>
                {measure.description ? <div className="text-xs text-muted-foreground">{measure.description}</div> : null}
              </TableCell>
              <TableCell>
                {measure.builtin ? (
                  <Badge variant="outline">built in</Badge>
                ) : measure.implemented ? (
                  <Badge variant="secondary">measures.py</Badge>
                ) : (
                  <Badge variant="destructive" title="Described in evaluation.yml but no function defines it">
                    no function
                  </Badge>
                )}
              </TableCell>
              <TableCell>{measure.usage_count}</TableCell>
              <TableCell>
                {!measure.builtin ? (
                  <div className="flex justify-end">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label="Remove description"
                      title="Remove its description from evaluation.yml (the function stays)"
                      onClick={() => mutate.mutate(() => api.removeMeasure(measure.name))}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div className="flex gap-2">
        <Input placeholder="new_measure_name" value={name} onChange={(e) => setName(e.target.value)} className="max-w-xs font-mono" />
        <Input placeholder="Label" value={label} onChange={(e) => setLabel(e.target.value)} className="max-w-xs" />
        <Button
          size="sm"
          disabled={!name.trim() || mutate.isPending}
          onClick={() =>
            mutate.mutate(async () => {
              await api.addMeasure({ name: name.trim(), label: label.trim() || null, stub: true })
              setName('')
              setLabel('')
            })
          }
        >
          Add with a stub
        </Button>
      </div>
      {mutate.error ? <p className="text-sm text-destructive">{(mutate.error as Error).message}</p> : null}
    </section>
  )
}
