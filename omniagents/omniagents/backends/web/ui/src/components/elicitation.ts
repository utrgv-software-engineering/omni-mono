// Pure helpers for the structured user-elicitation lane
// (elicitation_requested / elicitation_response / elicitation_resolved).
// See omniagents/rpc/protocol.md § "User Elicitation Flow" for the wire
// contract. Kept free of React so the validation and support rules are
// unit-testable and shared by every rendering surface.

export type ElicitationKind = 'question' | 'confirm' | 'select' | 'form' | 'url'

export type ElicitationItem = {
  type: 'elicitation'
  elicitation_id: string
  kind: string
  message: string
  title?: string
  input_schema?: Record<string, unknown>
  options?: unknown[]
  url?: string
  session_id?: string
  run_id?: string
  expires_at?: string
  persist_response?: boolean
}

export type ElicitationOption = {
  value: string | number | boolean
  label: string
  description?: string
}

export type FormField = {
  name: string
  label: string
  required: boolean
  kind: 'text' | 'number' | 'integer' | 'boolean' | 'enum'
  enumValues?: Array<string | number | boolean>
  description?: string
}

const SUPPORTED_KINDS = new Set<string>(['question', 'confirm', 'select', 'form', 'url'])

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v)
}

/** Normalize the open-record options array to renderable entries.
 *  Returns null when any entry carries no usable value (unsupported). */
export function optionEntries(options: unknown[] | undefined): ElicitationOption[] | null {
  if (!options || options.length === 0) return null
  const entries: ElicitationOption[] = []
  for (const raw of options) {
    if (typeof raw === 'string' || typeof raw === 'number' || typeof raw === 'boolean') {
      entries.push({ value: raw, label: String(raw) })
      continue
    }
    if (isRecord(raw) && (typeof raw.value === 'string' || typeof raw.value === 'number' || typeof raw.value === 'boolean')) {
      entries.push({
        value: raw.value,
        label: typeof raw.label === 'string' && raw.label ? raw.label : String(raw.value),
        description: typeof raw.description === 'string' ? raw.description : undefined,
      })
      continue
    }
    return null
  }
  return entries
}

/** Whether a select elicitation allows multiple choices. Conventionally
 *  signalled by input_schema.maxItems !== 1 on an array schema; default single. */
export function selectAllowsMultiple(item: Pick<ElicitationItem, 'input_schema'>): boolean {
  const schema = item.input_schema
  if (!isRecord(schema)) return false
  const selected = isRecord(schema.properties) ? schema.properties.selected : undefined
  const target = isRecord(selected) ? selected : schema
  return target.type === 'array' && target.maxItems !== 1
}

/** Flatten a form input_schema into renderable primitive fields.
 *  Returns null when the schema needs more than this renderer supports
 *  (nested objects, arrays, refs, ...) — callers must offer a typed
 *  decline instead of crashing. */
export function formFields(schema: Record<string, unknown> | undefined): FormField[] | null {
  if (!isRecord(schema)) return null
  if (schema.type !== undefined && schema.type !== 'object') return null
  const properties = schema.properties
  if (!isRecord(properties) || Object.keys(properties).length === 0) return null
  const required = new Set(Array.isArray(schema.required) ? schema.required.map(String) : [])
  const fields: FormField[] = []
  for (const [name, raw] of Object.entries(properties)) {
    if (!isRecord(raw)) return null
    const label = typeof raw.title === 'string' && raw.title ? raw.title : name
    const description = typeof raw.description === 'string' ? raw.description : undefined
    if (Array.isArray(raw.enum) && raw.enum.length > 0) {
      if (!raw.enum.every(v => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')) return null
      fields.push({ name, label, description, required: required.has(name), kind: 'enum', enumValues: raw.enum as Array<string | number | boolean> })
      continue
    }
    const type = raw.type
    if (type === 'string') fields.push({ name, label, description, required: required.has(name), kind: 'text' })
    else if (type === 'number') fields.push({ name, label, description, required: required.has(name), kind: 'number' })
    else if (type === 'integer') fields.push({ name, label, description, required: required.has(name), kind: 'integer' })
    else if (type === 'boolean') fields.push({ name, label, description, required: required.has(name), kind: 'boolean' })
    else return null
  }
  return fields
}

/** Can this client render the prompt? Unsupported prompts must surface a
 *  typed decline affordance (action: "decline", reason
 *  "unsupported_by_client"), never a crash or a silent drop. */
export function elicitationSupport(item: ElicitationItem): { supported: boolean; reason?: string } {
  if (!SUPPORTED_KINDS.has(item.kind)) {
    return { supported: false, reason: `unsupported elicitation kind "${item.kind}"` }
  }
  if (item.kind === 'select' && !optionEntries(item.options)) {
    return { supported: false, reason: 'select prompt carries no renderable options' }
  }
  if (item.kind === 'form' && !formFields(item.input_schema)) {
    return { supported: false, reason: 'form schema is not renderable by this client' }
  }
  if (item.kind === 'url' && !item.url) {
    return { supported: false, reason: 'url prompt carries no url' }
  }
  return { supported: true }
}

/** Client-side validation of an accept value, mirroring the server's
 *  contract so users get feedback before the round-trip. Returns
 *  human-readable problems; empty array means submittable. */
export function validateElicitationValue(item: ElicitationItem, value: Record<string, unknown>): string[] {
  const errors: string[] = []
  if (item.kind === 'question') {
    const text = value.text
    if (typeof text !== 'string' || !text.trim()) errors.push('An answer is required.')
  } else if (item.kind === 'confirm') {
    if (typeof value.confirmed !== 'boolean') errors.push('A yes/no choice is required.')
  } else if (item.kind === 'select') {
    const selected = value.selected
    if (!Array.isArray(selected) || selected.length === 0) {
      errors.push('Select at least one option.')
    } else {
      const allowed = optionEntries(item.options)
      if (allowed) {
        const values = new Set(allowed.map(o => o.value))
        for (const entry of selected) {
          if (!values.has(entry as string | number | boolean)) errors.push(`"${String(entry)}" is not one of the offered options.`)
        }
      }
    }
  } else if (item.kind === 'form') {
    const fields = formFields(item.input_schema)
    for (const field of fields ?? []) {
      const raw = value[field.name]
      const missing = raw === undefined || raw === null || raw === ''
      if (missing) {
        if (field.required) errors.push(`"${field.label}" is required.`)
        continue
      }
      if (field.kind === 'number' && typeof raw !== 'number') errors.push(`"${field.label}" must be a number.`)
      if (field.kind === 'integer' && (typeof raw !== 'number' || !Number.isInteger(raw))) errors.push(`"${field.label}" must be a whole number.`)
      if (field.kind === 'boolean' && typeof raw !== 'boolean') errors.push(`"${field.label}" must be a yes/no value.`)
      if (field.kind === 'enum' && field.enumValues && !field.enumValues.includes(raw as string | number | boolean)) errors.push(`"${field.label}" must be one of the offered values.`)
    }
  }
  // kind === 'url': acknowledgement needs no client-side validation.
  return errors
}
