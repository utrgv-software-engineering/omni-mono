import { describe, expect, it } from 'vitest'
import {
  elicitationSupport,
  formFields,
  optionEntries,
  selectAllowsMultiple,
  validateElicitationValue,
  type ElicitationItem,
} from './elicitation'

function item(overrides: Partial<ElicitationItem>): ElicitationItem {
  return {
    type: 'elicitation',
    elicitation_id: 'elc_test',
    kind: 'question',
    message: 'msg',
    ...overrides,
  }
}

describe('optionEntries', () => {
  it('normalizes bare values and value/label records', () => {
    expect(optionEntries(['a', 2, { value: 'b', label: 'Bee', description: 'buzz' }])).toEqual([
      { value: 'a', label: 'a' },
      { value: 2, label: '2' },
      { value: 'b', label: 'Bee', description: 'buzz' },
    ])
  })

  it('returns null for empty or non-comparable entries', () => {
    expect(optionEntries(undefined)).toBeNull()
    expect(optionEntries([])).toBeNull()
    expect(optionEntries([{ label: 'no value' }])).toBeNull()
  })
})

describe('formFields', () => {
  it('flattens primitive object schemas with titles and requireds', () => {
    expect(
      formFields({
        type: 'object',
        required: ['host'],
        properties: {
          host: { type: 'string', title: 'Host' },
          port: { type: 'integer' },
          tls: { type: 'boolean' },
          mode: { enum: ['a', 'b'] },
        },
      }),
    ).toEqual([
      { name: 'host', label: 'Host', description: undefined, required: true, kind: 'text' },
      { name: 'port', label: 'port', description: undefined, required: false, kind: 'integer' },
      { name: 'tls', label: 'tls', description: undefined, required: false, kind: 'boolean' },
      { name: 'mode', label: 'mode', description: undefined, required: false, kind: 'enum', enumValues: ['a', 'b'] },
    ])
  })

  it('rejects schemas beyond the renderable subset', () => {
    expect(formFields(undefined)).toBeNull()
    expect(formFields({ type: 'object' })).toBeNull()
    expect(formFields({ type: 'object', properties: { nested: { type: 'object' } } })).toBeNull()
    expect(formFields({ type: 'object', properties: { list: { type: 'array' } } })).toBeNull()
  })
})

describe('elicitationSupport', () => {
  it('supports the five kinds when their payloads are renderable', () => {
    expect(elicitationSupport(item({ kind: 'question' })).supported).toBe(true)
    expect(elicitationSupport(item({ kind: 'confirm' })).supported).toBe(true)
    expect(elicitationSupport(item({ kind: 'select', options: ['a'] })).supported).toBe(true)
    expect(
      elicitationSupport(item({ kind: 'form', input_schema: { type: 'object', properties: { a: { type: 'string' } } } })).supported,
    ).toBe(true)
    expect(elicitationSupport(item({ kind: 'url', url: 'https://x.test' })).supported).toBe(true)
  })

  it('declines unknown kinds and unrenderable payloads with a reason', () => {
    expect(elicitationSupport(item({ kind: 'hologram' }))).toEqual({
      supported: false,
      reason: 'unsupported elicitation kind "hologram"',
    })
    expect(elicitationSupport(item({ kind: 'select' })).supported).toBe(false)
    expect(elicitationSupport(item({ kind: 'form', input_schema: { type: 'object', properties: { deep: { type: 'object' } } } })).supported).toBe(false)
    expect(elicitationSupport(item({ kind: 'url' })).supported).toBe(false)
  })
})

describe('selectAllowsMultiple', () => {
  it('defaults to single-select', () => {
    expect(selectAllowsMultiple(item({ kind: 'select' }))).toBe(false)
  })
  it('honors an array selected schema without maxItems 1', () => {
    expect(selectAllowsMultiple(item({ input_schema: { properties: { selected: { type: 'array' } } } }))).toBe(true)
    expect(selectAllowsMultiple(item({ input_schema: { properties: { selected: { type: 'array', maxItems: 1 } } } }))).toBe(false)
  })
})

describe('validateElicitationValue', () => {
  it('mirrors the server per-kind contracts', () => {
    expect(validateElicitationValue(item({ kind: 'question' }), { text: 'hi' })).toEqual([])
    expect(validateElicitationValue(item({ kind: 'question' }), { text: '  ' }).length).toBe(1)
    expect(validateElicitationValue(item({ kind: 'confirm' }), { confirmed: false })).toEqual([])
    expect(validateElicitationValue(item({ kind: 'confirm' }), {}).length).toBe(1)
    const sel = item({ kind: 'select', options: ['a', { value: 'b' }] })
    expect(validateElicitationValue(sel, { selected: ['a', 'b'] })).toEqual([])
    expect(validateElicitationValue(sel, { selected: ['z'] }).length).toBe(1)
    expect(validateElicitationValue(sel, { selected: [] }).length).toBe(1)
  })

  it('validates renderable forms field by field', () => {
    const form = item({
      kind: 'form',
      input_schema: {
        type: 'object',
        required: ['host'],
        properties: { host: { type: 'string' }, port: { type: 'integer' }, mode: { enum: ['a', 'b'] } },
      },
    })
    expect(validateElicitationValue(form, { host: 'h', port: 8080, mode: 'a' })).toEqual([])
    expect(validateElicitationValue(form, { port: 1.5, mode: 'z' })).toEqual([
      '"host" is required.',
      '"port" must be a whole number.',
      '"mode" must be one of the offered values.',
    ])
  })

  it('accepts url acknowledgements without constraints', () => {
    expect(validateElicitationValue(item({ kind: 'url', url: 'https://x.test' }), {})).toEqual([])
  })
})
