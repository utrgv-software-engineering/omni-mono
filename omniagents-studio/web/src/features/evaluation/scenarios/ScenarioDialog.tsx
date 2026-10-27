import { Button } from '@omniagents/ui/button'
import { Checkbox } from '@omniagents/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@omniagents/ui/dialog'
import { Input } from '@omniagents/ui/input'
import { Label } from '@omniagents/ui/label'
import { NativeSelect, NativeSelectOption } from '@omniagents/ui/native-select'
import { Textarea } from '@omniagents/ui/textarea'
import { useEffect, useState } from 'react'

import type { FailureCategory, JsonValue } from '../../../types'
import type { Measure, Scenario } from '../../../types/evaluation'

type Kind = 'prompt' | 'conversation' | 'simulated'

/** Fields the form edits; the rest of a scenario is shown and kept as JSON. */
const FORM_FIELDS = new Set(['id', 'name', 'prompt', 'prompts', 'goal', 'tags', 'failure_categories', 'measures'])

function kindOf(scenario: Scenario | null): Kind {
  if (scenario?.goal) return 'simulated'
  if (scenario?.prompts && scenario.prompts.length > 0) return 'conversation'
  return 'prompt'
}

function measureName(entry: string | Record<string, JsonValue>): string {
  return typeof entry === 'string' ? entry : String(entry.name ?? '')
}

interface ScenarioDialogProps {
  open: boolean
  scenario: Scenario | null
  categories: FailureCategory[]
  measures: Measure[]
  saving: boolean
  error: string | null
  onSave: (scenario: Scenario) => void
  onClose: () => void
}

/** Add or edit one scenario: what is sent (a prompt, a scripted
 * conversation, or a goal for a simulated user), how it is labelled and
 * scored, and any other fields it carries. */
export function ScenarioDialog({ open, scenario, categories, measures, saving, error, onSave, onClose }: ScenarioDialogProps) {
  const [name, setName] = useState('')
  const [id, setId] = useState('')
  const [kind, setKind] = useState<Kind>('prompt')
  const [prompt, setPrompt] = useState('')
  const [prompts, setPrompts] = useState('')
  const [goal, setGoal] = useState('')
  const [tags, setTags] = useState('')
  const [labels, setLabels] = useState<string[]>([])
  const [scored, setScored] = useState<string[]>([])
  const [other, setOther] = useState('{}')
  const [otherError, setOtherError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setName(scenario?.name ?? '')
    setId(scenario?.id ? String(scenario.id) : '')
    setKind(kindOf(scenario))
    setPrompt(scenario?.prompt ?? '')
    setPrompts((scenario?.prompts ?? []).join('\n'))
    setGoal(scenario?.goal ?? '')
    setTags((scenario?.tags ?? []).join(', '))
    setLabels(scenario?.failure_categories ?? [])
    setScored((scenario?.measures ?? []).map(measureName))
    const rest = Object.fromEntries(Object.entries(scenario ?? {}).filter(([key]) => !FORM_FIELDS.has(key)))
    setOther(JSON.stringify(rest, null, 2))
    setOtherError(null)
  }, [open, scenario])

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value]

  const submit = () => {
    let rest: Record<string, JsonValue>
    try {
      rest = JSON.parse(other || '{}') as Record<string, JsonValue>
      if (typeof rest !== 'object' || rest === null || Array.isArray(rest)) throw new Error('an object')
    } catch {
      setOtherError('Other fields must be a JSON object')
      return
    }
    // Structured measure entries (with tiers or options) the form cannot show stay as written.
    const kept = (scenario?.measures ?? []).filter((entry) => typeof entry !== 'string' && scored.includes(measureName(entry)))
    const keptNames = new Set(kept.map(measureName))
    const next: Scenario = {
      ...rest,
      name: name.trim(),
      ...(id.trim() ? { id: id.trim() } : {}),
      ...(kind === 'prompt' ? { prompt } : {}),
      ...(kind === 'conversation' ? { prompts: prompts.split('\n').filter((line) => line.trim()) } : {}),
      ...(kind === 'simulated' ? { goal } : {}),
      ...(tags.trim() ? { tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean) } : {}),
      ...(labels.length ? { failure_categories: labels } : {}),
      ...(scored.length ? { measures: [...kept, ...scored.filter((m) => !keptNames.has(m))] } : {}),
    }
    onSave(next)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{scenario ? 'Edit scenario' : 'Add scenario'}</DialogTitle>
          <DialogDescription>Saved into the scenario file in place; its comments and other scenarios are kept.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="scenario-name">Name</Label>
              <Input id="scenario-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="scenario-id">Id (optional)</Label>
              <Input id="scenario-id" value={id} onChange={(e) => setId(e.target.value)} />
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="scenario-kind">What is sent</Label>
            <NativeSelect id="scenario-kind" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
              <NativeSelectOption value="prompt">One prompt</NativeSelectOption>
              <NativeSelectOption value="conversation">A scripted conversation (one message per line)</NativeSelectOption>
              <NativeSelectOption value="simulated">A simulated user with a goal</NativeSelectOption>
            </NativeSelect>
          </div>
          {kind === 'prompt' ? (
            <Textarea aria-label="Prompt" rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          ) : kind === 'conversation' ? (
            <Textarea aria-label="Messages" rows={5} value={prompts} onChange={(e) => setPrompts(e.target.value)} />
          ) : (
            <Textarea aria-label="Goal" rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} />
          )}
          <div className="grid gap-1.5">
            <Label htmlFor="scenario-tags">Tags (comma separated)</Label>
            <Input id="scenario-tags" value={tags} onChange={(e) => setTags(e.target.value)} />
          </div>
          <fieldset className="grid gap-2">
            <legend className="mb-1 text-sm font-medium">Failure categories it probes</legend>
            {categories.length === 0 ? <p className="text-xs text-muted-foreground">No failure categories yet.</p> : null}
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {categories.map((category) => (
                <label key={category.id} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={labels.includes(category.id) || labels.includes(category.name)}
                    onCheckedChange={() => setLabels((current) => toggle(current.filter((l) => l !== category.name), category.id))}
                  />
                  {category.name}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="grid gap-2">
            <legend className="mb-1 text-sm font-medium">Measures that score it</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {measures.map((measure) => (
                <label key={measure.name} className="flex items-center gap-2 text-sm">
                  <Checkbox checked={scored.includes(measure.name)} onCheckedChange={() => setScored((current) => toggle(current, measure.name))} />
                  <span className="font-mono text-xs">{measure.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-1.5">
            <Label htmlFor="scenario-other">Other fields (JSON): expect, dimensions, persona, max_exchanges…</Label>
            <Textarea id="scenario-other" rows={6} className="font-mono text-xs" value={other} onChange={(e) => setOther(e.target.value)} />
            {otherError ? <p className="text-xs text-destructive">{otherError}</p> : null}
          </div>
          {error ? <p className="whitespace-pre-wrap text-sm text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
