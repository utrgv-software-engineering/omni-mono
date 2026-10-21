import React, { useCallback, useMemo, useState } from 'react'
import {
  ElicitationItem,
  ElicitationOption,
  FormField,
  elicitationSupport,
  formFields,
  optionEntries,
  selectAllowsMultiple,
  validateElicitationValue,
} from './elicitation'

export type ElicitationRespond = (
  elicitationId: string,
  action: 'accept' | 'decline' | 'cancel',
  value?: Record<string, unknown>,
  reason?: string,
) => void

// Transcript card for a pending elicitation prompt. Renders a real input
// surface per kind (free text, confirm, option select, schema-driven
// form, URL action) with client-side validation mirroring the server's
// contract; prompts this client cannot render surface a typed decline
// affordance instead of crashing. First response across tabs wins — the
// card is dismissed by elicitation_resolved, not by local submission.
export function ElicitationCard({ item, onRespond }: { item: ElicitationItem; onRespond?: ElicitationRespond }) {
  const support = useMemo(() => elicitationSupport(item), [item])
  const [errors, setErrors] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)

  const respond = useCallback(
    (action: 'accept' | 'decline' | 'cancel', value?: Record<string, unknown>, reason?: string) => {
      if (action === 'accept') {
        const problems = validateElicitationValue(item, value || {})
        if (problems.length > 0) {
          setErrors(problems)
          return
        }
      }
      setErrors([])
      setSubmitting(true)
      onRespond?.(item.elicitation_id, action, value, reason)
    },
    [item, onRespond],
  )

  const headingId = `elicit-${item.elicitation_id}-heading`
  const messageId = `elicit-${item.elicitation_id}-message`

  return (
    <section
      aria-labelledby={headingId}
      aria-describedby={messageId}
      className="rounded-md border border-tweetBlue bg-bgCardAlt p-3"
    >
      <h3 id={headingId} className="text-sm font-semibold text-tweetBlue">
        {item.title || defaultTitle(item.kind)}
      </h3>
      <p id={messageId} className="mt-1 text-sm text-textPrimary whitespace-pre-wrap">
        {item.message}
      </p>
      {item.expires_at ? (
        <p className="mt-1 text-xs text-textSubtle">Expires {item.expires_at}</p>
      ) : null}
      <div className="mt-3">
        {support.supported ? (
          <ElicitationBody item={item} disabled={submitting} onRespond={respond} />
        ) : (
          <UnsupportedBody reason={support.reason || 'unsupported prompt'} disabled={submitting} onDecline={() => respond('decline', undefined, 'unsupported_by_client')} />
        )}
      </div>
      {errors.length > 0 ? (
        <ul role="alert" className="mt-2 space-y-0.5 text-xs text-errorRed">
          {errors.map((err, i) => (
            <li key={i}>{err}</li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function defaultTitle(kind: string): string {
  switch (kind) {
    case 'confirm': return 'Confirmation needed'
    case 'select': return 'Choose an option'
    case 'form': return 'Input needed'
    case 'url': return 'Action required'
    default: return 'Question'
  }
}

function ElicitationBody({ item, disabled, onRespond }: { item: ElicitationItem; disabled: boolean; onRespond: (action: 'accept' | 'decline' | 'cancel', value?: Record<string, unknown>, reason?: string) => void }) {
  if (item.kind === 'question') return <QuestionBody item={item} disabled={disabled} onRespond={onRespond} />
  if (item.kind === 'confirm') return <ConfirmBody disabled={disabled} onRespond={onRespond} />
  if (item.kind === 'select') return <SelectBody item={item} disabled={disabled} onRespond={onRespond} />
  if (item.kind === 'form') return <FormBody item={item} disabled={disabled} onRespond={onRespond} />
  return <UrlBody item={item} disabled={disabled} onRespond={onRespond} />
}

function ActionRow({ disabled, onDecline, submitLabel, onSubmit }: { disabled: boolean; onDecline: () => void; submitLabel: string; onSubmit?: () => void }) {
  return (
    <div className="mt-3 flex gap-2 justify-end">
      <button
        type="button"
        disabled={disabled}
        className="px-3 py-1.5 text-xs rounded-md border border-errorRed text-errorRed bg-transparent hover:bg-errorRed/20 disabled:opacity-50"
        onClick={onDecline}
      >
        Decline
      </button>
      <button
        type={onSubmit ? 'button' : 'submit'}
        disabled={disabled}
        className="px-3 py-1.5 text-xs rounded-md bg-tweetBlue hover:brightness-110 text-white disabled:opacity-50"
        onClick={onSubmit}
      >
        {submitLabel}
      </button>
    </div>
  )
}

function QuestionBody({ item, disabled, onRespond }: { item: ElicitationItem; disabled: boolean; onRespond: (action: 'accept' | 'decline' | 'cancel', value?: Record<string, unknown>, reason?: string) => void }) {
  const [text, setText] = useState('')
  const inputId = `elicit-${item.elicitation_id}-answer`
  return (
    <form
      onSubmit={e => {
        e.preventDefault()
        onRespond('accept', { text })
      }}
    >
      <label htmlFor={inputId} className="block text-xs font-medium text-textSubtle">
        Your answer
      </label>
      <textarea
        id={inputId}
        value={text}
        disabled={disabled}
        rows={2}
        className="mt-1 w-full rounded-md border border-bgCardAlt bg-bgColumn p-2 text-sm text-textPrimary"
        onChange={e => setText(e.target.value)}
      />
      <ActionRow disabled={disabled} submitLabel="Send answer" onDecline={() => onRespond('decline')} />
    </form>
  )
}

function ConfirmBody({ disabled, onRespond }: { disabled: boolean; onRespond: (action: 'accept' | 'decline' | 'cancel', value?: Record<string, unknown>, reason?: string) => void }) {
  return (
    <div className="mt-1 flex gap-2 justify-end">
      <button
        type="button"
        disabled={disabled}
        className="px-3 py-1.5 text-xs rounded-md border border-errorRed text-errorRed bg-transparent hover:bg-errorRed/20 disabled:opacity-50"
        onClick={() => onRespond('accept', { confirmed: false })}
      >
        No
      </button>
      <button
        type="button"
        disabled={disabled}
        className="px-3 py-1.5 text-xs rounded-md bg-tweetBlue hover:brightness-110 text-white disabled:opacity-50"
        onClick={() => onRespond('accept', { confirmed: true })}
      >
        Yes
      </button>
    </div>
  )
}

function SelectBody({ item, disabled, onRespond }: { item: ElicitationItem; disabled: boolean; onRespond: (action: 'accept' | 'decline' | 'cancel', value?: Record<string, unknown>, reason?: string) => void }) {
  const entries = useMemo(() => optionEntries(item.options) || [], [item.options])
  const multiple = useMemo(() => selectAllowsMultiple(item), [item])
  const [selected, setSelected] = useState<Array<string | number | boolean>>([])
  const toggle = useCallback(
    (value: string | number | boolean) => {
      setSelected(prev => {
        if (multiple) {
          return prev.includes(value) ? prev.filter(v => v !== value) : [...prev, value]
        }
        return [value]
      })
    },
    [multiple],
  )
  const name = `elicit-${item.elicitation_id}-options`
  return (
    <form
      onSubmit={e => {
        e.preventDefault()
        onRespond('accept', { selected })
      }}
    >
      <fieldset className="space-y-1" disabled={disabled}>
        <legend className="text-xs font-medium text-textSubtle">{multiple ? 'Select all that apply' : 'Select one'}</legend>
        {entries.map((option: ElicitationOption) => {
          const id = `${name}-${String(option.value)}`
          return (
            <div key={id} className="flex items-start gap-2">
              <input
                id={id}
                type={multiple ? 'checkbox' : 'radio'}
                name={name}
                checked={selected.includes(option.value)}
                className="mt-0.5"
                onChange={() => toggle(option.value)}
              />
              <label htmlFor={id} className="text-sm text-textPrimary">
                {option.label}
                {option.description ? <span className="block text-xs text-textSubtle">{option.description}</span> : null}
              </label>
            </div>
          )
        })}
      </fieldset>
      <ActionRow disabled={disabled} submitLabel="Submit choice" onDecline={() => onRespond('decline')} />
    </form>
  )
}

function FormBody({ item, disabled, onRespond }: { item: ElicitationItem; disabled: boolean; onRespond: (action: 'accept' | 'decline' | 'cancel', value?: Record<string, unknown>, reason?: string) => void }) {
  const fields = useMemo(() => formFields(item.input_schema) || [], [item.input_schema])
  const [values, setValues] = useState<Record<string, unknown>>({})
  const setField = useCallback((nameKey: string, value: unknown) => {
    setValues(prev => ({ ...prev, [nameKey]: value }))
  }, [])
  return (
    <form
      onSubmit={e => {
        e.preventDefault()
        const cleaned: Record<string, unknown> = {}
        for (const [key, value] of Object.entries(values)) {
          if (value !== undefined && value !== '') cleaned[key] = value
        }
        onRespond('accept', cleaned)
      }}
    >
      <div className="space-y-2">
        {fields.map((field: FormField) => (
          <FormFieldInput key={field.name} idPrefix={`elicit-${item.elicitation_id}`} field={field} value={values[field.name]} disabled={disabled} onChange={setField} />
        ))}
      </div>
      <ActionRow disabled={disabled} submitLabel="Submit" onDecline={() => onRespond('decline')} />
    </form>
  )
}

function FormFieldInput({ idPrefix, field, value, disabled, onChange }: { idPrefix: string; field: FormField; value: unknown; disabled: boolean; onChange: (name: string, value: unknown) => void }) {
  const id = `${idPrefix}-${field.name}`
  const label = (
    <label htmlFor={id} className="block text-xs font-medium text-textSubtle">
      {field.label}
      {field.required ? <span aria-hidden="true"> *</span> : null}
    </label>
  )
  if (field.kind === 'boolean') {
    return (
      <div className="flex items-center gap-2">
        <input id={id} type="checkbox" checked={value === true} disabled={disabled} onChange={e => onChange(field.name, e.target.checked)} />
        <label htmlFor={id} className="text-sm text-textPrimary">{field.label}</label>
      </div>
    )
  }
  if (field.kind === 'enum') {
    return (
      <div>
        {label}
        <select
          id={id}
          value={value === undefined ? '' : String(value)}
          disabled={disabled}
          className="mt-1 w-full rounded-md border border-bgCardAlt bg-bgColumn p-2 text-sm text-textPrimary"
          onChange={e => {
            const match = (field.enumValues || []).find(v => String(v) === e.target.value)
            onChange(field.name, match)
          }}
        >
          <option value="">Choose…</option>
          {(field.enumValues || []).map(entry => (
            <option key={String(entry)} value={String(entry)}>
              {String(entry)}
            </option>
          ))}
        </select>
      </div>
    )
  }
  const numeric = field.kind === 'number' || field.kind === 'integer'
  return (
    <div>
      {label}
      <input
        id={id}
        type={numeric ? 'number' : 'text'}
        step={field.kind === 'integer' ? 1 : undefined}
        value={value === undefined ? '' : String(value)}
        disabled={disabled}
        aria-describedby={field.description ? `${id}-desc` : undefined}
        className="mt-1 w-full rounded-md border border-bgCardAlt bg-bgColumn p-2 text-sm text-textPrimary"
        onChange={e => {
          if (!numeric) {
            onChange(field.name, e.target.value)
            return
          }
          const parsed = e.target.value === '' ? undefined : Number(e.target.value)
          onChange(field.name, parsed !== undefined && Number.isNaN(parsed) ? e.target.value : parsed)
        }}
      />
      {field.description ? (
        <p id={`${id}-desc`} className="mt-0.5 text-xs text-textSubtle">{field.description}</p>
      ) : null}
    </div>
  )
}

function UrlBody({ item, disabled, onRespond }: { item: ElicitationItem; disabled: boolean; onRespond: (action: 'accept' | 'decline' | 'cancel', value?: Record<string, unknown>, reason?: string) => void }) {
  return (
    <div>
      <a
        href={item.url}
        target="_blank"
        rel="noreferrer noopener"
        className="inline-block text-sm text-tweetBlue underline break-all"
      >
        {item.url}
      </a>
      <ActionRow
        disabled={disabled}
        submitLabel="I've done this"
        onDecline={() => onRespond('decline')}
        onSubmit={() => onRespond('accept', { completed: true })}
      />
    </div>
  )
}

function UnsupportedBody({ reason, disabled, onDecline }: { reason: string; disabled: boolean; onDecline: () => void }) {
  return (
    <div>
      <p className="text-xs text-textSubtle">
        This client can't render this request ({reason}). Decline it here, or answer from a client that supports it.
      </p>
      <div className="mt-2 flex justify-end">
        <button
          type="button"
          disabled={disabled}
          className="px-3 py-1.5 text-xs rounded-md border border-errorRed text-errorRed bg-transparent hover:bg-errorRed/20 disabled:opacity-50"
          onClick={onDecline}
        >
          Decline
        </button>
      </div>
    </div>
  )
}
