import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, JSX } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { useToastStore } from '../../stores/toastStore'
import { api, type PlaygroundSessionPayload } from '../../lib/api'
import type {
  ModelSettingDefinition,
  PlaygroundMetadata,
  PlaygroundToolDefinition,
  PlaygroundSessionResponse,
  PlaygroundSafeAgentOptions,
  AgentUpdatePayload,
} from '../../types'
import {
  usePlaygroundSession,
  type PlaygroundApprovalDecision,
  type PlaygroundChatEvent,
  type PlaygroundConnectionState,
  type PlaygroundUsageTotals,
} from './usePlaygroundSession'
import { MarkdownMessage } from '../../shared/components/MarkdownMessage'

export interface PlaygroundContext {
  projectName: string
  sessionId: string
  statusMessage: string
  agentKey?: string | null
  agentLabel?: string
}

export interface PlaygroundViewProps {
  onUpdateContext: (context: PlaygroundContext) => void
}

interface AgentWorkbenchProps {
  metadataQueryKey: unknown[]
  metadataFetcher: () => Promise<PlaygroundMetadata>
  configureSession: (payload: PlaygroundSessionPayload) => Promise<PlaygroundSessionResponse>
  onUpdateContext: (context: PlaygroundContext) => void
  agentKey?: string | null
  heading?: string
  agentLabel?: string
  containerPadding?: boolean
}

type OutputFieldType = 'string' | 'number' | 'integer' | 'boolean' | 'array' | 'object'

interface OutputField {
  id: string
  name: string
  type: OutputFieldType
  required: boolean
}

const OUTPUT_FIELD_OPTIONS: Array<{ value: OutputFieldType; label: string }> = [
  { value: 'string', label: 'Text' },
  { value: 'number', label: 'Number (float)' },
  { value: 'integer', label: 'Number (integer)' },
  { value: 'boolean', label: 'True/false' },
  { value: 'array', label: 'List' },
  { value: 'object', label: 'Object' },
]

const supportedJsonTypes = new Set(['string', 'number', 'integer', 'boolean', 'array', 'object'])

const generateFieldId = () => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return Math.random().toString(36).slice(2)
}

const mapJsonTypeToFieldType = (value: unknown): OutputFieldType => {
  if (typeof value === 'string') {
    const normalized = value.toLowerCase()
    if (supportedJsonTypes.has(normalized)) {
      return normalized as OutputFieldType
    }
    if (normalized === 'dict' || normalized === 'dictionary') {
      return 'object'
    }
    if (normalized === 'list' || normalized === 'array') {
      return 'array'
    }
    if (normalized === 'bool') {
      return 'boolean'
    }
  }
  return 'string'
}

const schemaToFields = (schema: Record<string, unknown> | null | undefined): OutputField[] => {
  if (!schema || typeof schema !== 'object') {
    return []
  }
  const typeValue = typeof schema['type'] === 'string' ? String(schema['type']).toLowerCase() : undefined
  if (typeValue && typeValue !== 'object' && typeValue !== 'dict' && typeValue !== 'dictionary') {
    return []
  }
  const propertiesRaw = schema['properties']
  if (!propertiesRaw || typeof propertiesRaw !== 'object') {
    return []
  }
  const requiredNames = new Set<string>()
  const requiredRaw = schema['required']
  if (Array.isArray(requiredRaw)) {
    for (const item of requiredRaw) {
      if (typeof item === 'string') {
        requiredNames.add(item)
      }
    }
  }
  const fields: OutputField[] = []
  for (const [name, definition] of Object.entries(propertiesRaw)) {
    if (typeof name !== 'string') {
      continue
    }
    if (!definition || typeof definition !== 'object') {
      continue
    }
    const fieldType = mapJsonTypeToFieldType((definition as Record<string, unknown>)['type'])
    fields.push({
      id: generateFieldId(),
      name,
      type: fieldType,
      required: requiredNames.has(name),
    })
  }
  return fields
}

const jsonTypeForField = (fieldType: OutputFieldType): string => {
  if (fieldType === 'object') {
    return 'object'
  }
  if (fieldType === 'array') {
    return 'array'
  }
  if (fieldType === 'boolean') {
    return 'boolean'
  }
  if (fieldType === 'integer') {
    return 'integer'
  }
  if (fieldType === 'number') {
    return 'number'
  }
  return 'string'
}

const fieldsToSchema = (fields: OutputField[]): Record<string, unknown> | null => {
  const properties: Record<string, unknown> = {}
  const required: string[] = []
  for (const field of fields) {
    const name = field.name.trim()
    if (!name) {
      continue
    }
    const entry: Record<string, unknown> = {
      type: jsonTypeForField(field.type),
    }
    if (field.type === 'array') {
      entry.items = {}
    }
    properties[name] = entry
    if (field.required) {
      required.push(name)
    }
  }
  const keys = Object.keys(properties)
  if (keys.length === 0) {
    return null
  }
  const schema: Record<string, unknown> = {
    type: 'object',
    properties,
    additionalProperties: false,
  }
  if (required.length > 0) {
    schema.required = required
  }
  return schema
}

type ToolSelection = Record<string, boolean>

type ModelSettingsState = Record<string, number | string | boolean>

type SafeOptionsState = {
  skip_approvals: boolean
  halt_on_rejection: boolean
  safe_tool_names: string[]
  safe_tool_patterns: string[]
}

const createDefaultSafeOptions = (): SafeOptionsState => ({
  skip_approvals: false,
  halt_on_rejection: true,
  safe_tool_names: [],
  safe_tool_patterns: [],
})

const sanitizeSafeOptions = (options: PlaygroundSafeAgentOptions | undefined, toolNames: string[]): SafeOptionsState => {
  if (!options) {
    return createDefaultSafeOptions()
  }
  const names = Array.isArray(options.safe_tool_names)
    ? options.safe_tool_names
        .filter((name): name is string => typeof name === 'string')
        .filter((name, index, self) => self.indexOf(name) === index && toolNames.includes(name))
    : []
  const patterns = Array.isArray(options.safe_tool_patterns)
    ? options.safe_tool_patterns
        .filter((pattern): pattern is string => typeof pattern === 'string')
        .map((pattern) => pattern.trim())
        .filter((pattern, index, self) => pattern.length > 0 && self.indexOf(pattern) === index)
    : []
  return {
    skip_approvals: Boolean(options.skip_approvals),
    halt_on_rejection: options.halt_on_rejection === undefined ? true : Boolean(options.halt_on_rejection),
    safe_tool_names: names,
    safe_tool_patterns: patterns,
  }
}

type OutputFieldSummary = {
  name: string
  type: OutputFieldType
  required: boolean
}

type SerializedAgentConfig = {
  model: string
  instructions: string
  tools: string[]
  modelSettings: ModelSettingsState
  useSafeAgent: boolean
  safeOptions: SafeOptionsState
  guardrails: {
    input: string[]
    output: string[]
  }
  output: {
    selection: string
    strict: boolean
    fields: OutputFieldSummary[]
  }
}

const serializeAgentConfig = (
  model: string,
  instructions: string,
  tools: string[],
  modelSettings: ModelSettingsState,
  useSafeAgent: boolean,
  safeOptions: SafeOptionsState,
  inputGuardrails: string[],
  outputGuardrails: string[],
  outputTypeSelection: string,
  outputTypeStrict: boolean,
  outputFields: OutputField[],
): SerializedAgentConfig => ({
  model: model,
  instructions,
  tools: [...tools],
  modelSettings: { ...modelSettings },
  useSafeAgent,
  safeOptions: {
    skip_approvals: safeOptions.skip_approvals,
    halt_on_rejection: safeOptions.halt_on_rejection,
    safe_tool_names: [...safeOptions.safe_tool_names],
    safe_tool_patterns: [...safeOptions.safe_tool_patterns],
  },
  guardrails: {
    input: [...inputGuardrails],
    output: [...outputGuardrails],
  },
  output: {
    selection: outputTypeSelection,
    strict: outputTypeStrict,
    fields: outputFields.map((field) => ({
      name: field.name,
      type: field.type,
      required: field.required,
    })),
  },
})

const prepareComparison = (snapshot: SerializedAgentConfig) => {
  const normalizedSettings = Object.entries(snapshot.modelSettings)
    .map(([key, value]) => [key, value] as [string, number | string | boolean])
    .sort((a, b) => a[0].localeCompare(b[0]))
  const normalizeList = (list: string[]) => list.map((item) => item.trim()).filter((item) => item.length > 0).sort()
  const normalizedFields = snapshot.output.fields
    .map((field) => ({
      name: field.name.trim(),
      type: field.type,
      required: field.required,
    }))
    .sort((a, b) => {
      const nameCompare = a.name.localeCompare(b.name)
      if (nameCompare !== 0) {
        return nameCompare
      }
      const typeCompare = a.type.localeCompare(b.type)
      if (typeCompare !== 0) {
        return typeCompare
      }
      if (a.required === b.required) {
        return 0
      }
      return a.required ? -1 : 1
    })
  return {
    model: snapshot.model.trim(),
    instructions: snapshot.instructions,
    tools: normalizeList(snapshot.tools),
    modelSettings: normalizedSettings,
    useSafeAgent: snapshot.useSafeAgent,
    safeOptions: {
      skip_approvals: snapshot.safeOptions.skip_approvals,
      halt_on_rejection: snapshot.safeOptions.halt_on_rejection,
      safe_tool_names: normalizeList(snapshot.safeOptions.safe_tool_names),
      safe_tool_patterns: normalizeList(snapshot.safeOptions.safe_tool_patterns),
    },
    guardrails: {
      input: normalizeList(snapshot.guardrails.input),
      output: normalizeList(snapshot.guardrails.output),
    },
    output: {
      selection: snapshot.output.selection,
      strict: snapshot.output.strict,
      fields: normalizedFields,
    },
  }
}

export function AgentWorkbench({
  metadataQueryKey,
  metadataFetcher,
  configureSession,
  onUpdateContext,
  agentKey = null,
  heading = 'Configure your playground agent',
  agentLabel,
  containerPadding = true,
}: AgentWorkbenchProps) {
  const { data, isLoading, isError, refetch } = useQuery<PlaygroundMetadata>({
    queryKey: metadataQueryKey,
    queryFn: metadataFetcher,
    staleTime: 5 * 60 * 1000,
  })

  const pushToast = useToastStore((state) => state.pushToast)
  const queryClient = useQueryClient()
  const [initialComparison, setInitialComparison] = useState<string | null>(null)

  const [modelInput, setModelInput] = useState('')
  const [instructions, setInstructions] = useState('')
  const [instructionsDirty, setInstructionsDirty] = useState(false)
  const [selectedTools, setSelectedTools] = useState<string[]>([])
  const [toolSelection, setToolSelection] = useState<ToolSelection>({})
  const [toolsDirty, setToolsDirty] = useState(false)
  const [sessionId, setSessionId] = useState('')
  const [sessionStatus, setSessionStatus] = useState('')
  const [statusSpinner, setStatusSpinner] = useState(false)
  const [statusSeverity, setStatusSeverity] = useState<'info' | 'error'>('info')
  const [formError, setFormError] = useState('')
  const [defaultsApplied, setDefaultsApplied] = useState(false)
  const [toolModalOpen, setToolModalOpen] = useState(false)
  const [configDirty, setConfigDirty] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [modelSettings, setModelSettings] = useState<ModelSettingsState>({})
  const [modelSettingsDirty, setModelSettingsDirty] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [useSafeAgent, setUseSafeAgent] = useState(false)
  const [safeOptions, setSafeOptions] = useState<SafeOptionsState>(createDefaultSafeOptions)
  const [, setSafeOptionsDirty] = useState(false)
  const [safePatternInput, setSafePatternInput] = useState('')
  const [selectedInputGuardrails, setSelectedInputGuardrails] = useState<string[]>([])
  const [selectedOutputGuardrails, setSelectedOutputGuardrails] = useState<string[]>([])
  const [guardrailsDirty, setGuardrailsDirty] = useState(false)
  const [outputTypeSelection, setOutputTypeSelection] = useState('default')
  const [outputTypeStrict, setOutputTypeStrict] = useState(true)
  const [outputFields, setOutputFields] = useState<OutputField[]>([])
  const [outputFieldsError, setOutputFieldsError] = useState<string | null>(null)
  const [outputConfigDirty, setOutputConfigDirty] = useState(false)

  const tools = useMemo(() => (data?.tools ?? []) as PlaygroundToolDefinition[], [data])
  const modelSettingsSchema = useMemo(() => data?.model_settings_schema ?? [], [data])
  const availableInputGuardrails = useMemo(() => data?.guardrails?.input ?? [], [data])
  const availableOutputGuardrails = useMemo(() => data?.guardrails?.output ?? [], [data])
  const structuredOutputConfig = useMemo(() => data?.structured_output, [data])
  const structuredOutputOptions = structuredOutputConfig?.options ?? []
  const defaultSchemaPreview = useMemo(() => {
    if (!structuredOutputConfig?.default_schema) {
      return null
    }
    try {
      return JSON.stringify(structuredOutputConfig.default_schema, null, 2)
    } catch {
      return null
    }
  }, [structuredOutputConfig])
  const schemaPreview = useMemo(() => {
    if (outputTypeSelection !== 'dict') {
      return null
    }
    const schema = fieldsToSchema(outputFields)
    if (!schema) {
      return null
    }
    try {
      return JSON.stringify(schema, null, 2)
    } catch {
      return null
    }
  }, [outputTypeSelection, outputFields])

  const {
    events,
    usageTotals,
    connectionState,
    connectionError,
    sendPrompt,
    stopRun,
    isRunning,
    respondToApproval,
    resetConversation,
  } = usePlaygroundSession(sessionId || null, data?.playground_ws_url, agentKey ?? undefined)

  const connectionStateRef = useRef<PlaygroundConnectionState>(connectionState)

  useEffect(() => {
    connectionStateRef.current = connectionState
  }, [connectionState])

  const applyStatus = useCallback((text: string, options?: { spinner?: boolean; severity?: 'info' | 'error' }) => {
    setSessionStatus(text)
    setStatusSpinner(options?.spinner ?? false)
    setStatusSeverity(options?.severity ?? 'info')
  }, [])

  const clearStatus = useCallback(() => {
    setSessionStatus('')
    setStatusSpinner(false)
    setStatusSeverity('info')
  }, [])

  useEffect(() => {
    if (!data?.available || defaultsApplied) {
      return
    }
    const shouldPrefill = Boolean(agentKey)
    const defaultModel = data?.default_model ?? ''
    const resolvedInstructions = shouldPrefill ? data?.default_instructions ?? '' : ''
    const availableToolNames = tools.map((tool) => tool.name)
    const resolvedTools = shouldPrefill ? Array.from(new Set(data?.default_tools ?? [])) : []
    const resolvedModelSettings = shouldPrefill ? data?.default_model_settings ?? {} : {}
    const resolvedUseSafe = Boolean(data?.safe_defaults?.use_safe_agent)
    const resolvedSafeOptions = sanitizeSafeOptions(
      data?.safe_defaults?.options,
      shouldPrefill ? resolvedTools : availableToolNames,
    )
    const resolvedInputGuardrails = shouldPrefill ? data?.guardrails?.default_input ?? [] : []
    const resolvedOutputGuardrails = shouldPrefill ? data?.guardrails?.default_output ?? [] : []
    const resolvedOutputFields = shouldPrefill ? schemaToFields(structuredOutputConfig?.default_schema) : []
    const resolvedOutputStrict = structuredOutputConfig ? structuredOutputConfig.default_strict ?? true : true
    const baselineSafeOptions = resolvedSafeOptions
    const baselineInputGuardrails = shouldPrefill ? resolvedInputGuardrails : []
    const baselineOutputGuardrails = shouldPrefill ? resolvedOutputGuardrails : []
    const baselineOutputFields = shouldPrefill ? resolvedOutputFields : []
    setModelInput(defaultModel)
    if (shouldPrefill) {
      setInstructions(resolvedInstructions)
      setInstructionsDirty(false)
      updateSelection(resolvedTools, false)
      setToolsDirty(false)
      setModelSettings(resolvedModelSettings)
      setModelSettingsDirty(false)
      setUseSafeAgent(resolvedUseSafe)
      setSafeOptions(baselineSafeOptions)
      setSafeOptionsDirty(false)
      setSafePatternInput(baselineSafeOptions.safe_tool_patterns.join('\n'))
      setSelectedInputGuardrails(baselineInputGuardrails)
      setSelectedOutputGuardrails(baselineOutputGuardrails)
      setGuardrailsDirty(false)
      setOutputFields(baselineOutputFields)
    } else {
      setInstructions('')
      setInstructionsDirty(false)
      updateSelection([], false)
      setToolsDirty(false)
      setModelSettings({})
      setModelSettingsDirty(false)
      setUseSafeAgent(resolvedUseSafe)
      setSafeOptions(baselineSafeOptions)
      setSafeOptionsDirty(false)
      setSafePatternInput(baselineSafeOptions.safe_tool_patterns.join('\n'))
      setSelectedInputGuardrails([])
      setSelectedOutputGuardrails([])
      setGuardrailsDirty(false)
      setOutputFields([])
    }
    setOutputTypeSelection('default')
    setOutputTypeStrict(resolvedOutputStrict)
    setOutputFieldsError(null)
    setOutputConfigDirty(false)
    setShowAdvanced(false)
    setSessionId('')
    setSessionStatus('')
    setStatusSpinner(false)
    setStatusSeverity('info')
    setConfigDirty(false)
    const snapshot = serializeAgentConfig(
      defaultModel,
      resolvedInstructions,
      resolvedTools,
      resolvedModelSettings,
      resolvedUseSafe,
      baselineSafeOptions,
      baselineInputGuardrails,
      baselineOutputGuardrails,
      'default',
      resolvedOutputStrict,
      baselineOutputFields,
    )
    setInitialComparison(JSON.stringify(prepareComparison(snapshot)))
    setDefaultsApplied(true)
  }, [agentKey, data, defaultsApplied, structuredOutputConfig, tools, updateSelection])

  useEffect(() => {
    const projectName = data?.project_name || ''
    const resolvedLabel = agentLabel ?? data?.agent_label ?? undefined
    if (!data) {
      onUpdateContext({ projectName, sessionId, statusMessage: '', agentKey, agentLabel: resolvedLabel })
      return
    }
    if (!data.available) {
      onUpdateContext({
        projectName,
        sessionId,
        statusMessage: 'Playground is unavailable for this server',
        agentKey,
        agentLabel: resolvedLabel,
      })
      return
    }
    const parts: string[] = []
    if (sessionStatus) {
      parts.push(sessionStatus)
    }
    if (isRunning && sessionStatus !== 'Run in progress…') {
      parts.push('Run in progress…')
    }
    if (connectionError) {
      parts.push(`Error: ${connectionError}`)
    }
    onUpdateContext({
      projectName,
      sessionId,
      statusMessage: parts.join(' · '),
      agentKey,
      agentLabel: resolvedLabel,
    })
  }, [data, sessionStatus, sessionId, onUpdateContext, isRunning, connectionError, agentKey, agentLabel])

  useEffect(() => {
    setSelectedInputGuardrails((prev) => prev.filter((name) => availableInputGuardrails.some((item) => item.name === name)))
  }, [availableInputGuardrails])

  useEffect(() => {
    setSelectedOutputGuardrails((prev) => prev.filter((name) => availableOutputGuardrails.some((item) => item.name === name)))
  }, [availableOutputGuardrails])

  const sessionMutation = useMutation({
    mutationFn: (payload: PlaygroundSessionPayload) => configureSession(payload),
  })

  const agentUpdateMutation = useMutation({
    mutationFn: (payload: AgentUpdatePayload) => {
      if (!agentKey) {
        throw new Error('Agent key is required')
      }
      return api.updateAgent(agentKey, payload)
    },
  })

  const handleOutputTypeChange = useCallback(
    (value: string) => {
      setOutputTypeSelection(value)
      if (value === 'default') {
        setOutputTypeStrict(structuredOutputConfig?.default_strict ?? true)
        setOutputFields([])
        setOutputFieldsError(null)
        setOutputConfigDirty(false)
      } else if (value === 'none') {
        setOutputTypeStrict(false)
        setOutputFieldsError(null)
        setOutputFields([])
        setOutputConfigDirty(true)
      } else if (value === 'dict') {
        setOutputFieldsError(null)
        setOutputConfigDirty(true)
        setOutputFields((prev) => {
          if (prev.length === 0) {
            return [
              {
                id: generateFieldId(),
                name: '',
                type: 'string',
                required: true,
              },
            ]
          }
          return prev
        })
      } else {
        setOutputFieldsError(null)
        setOutputFields([])
        setOutputConfigDirty(true)
      }
      setConfigDirty(true)
    },
    [structuredOutputConfig]
  )

  const addOutputField = useCallback(() => {
    setOutputFields((prev) => [
      ...prev,
      {
        id: generateFieldId(),
        name: '',
        type: 'string',
        required: true,
      },
    ])
    setOutputFieldsError(null)
    setOutputConfigDirty(true)
    setConfigDirty(true)
  }, [])

  const updateOutputField = useCallback((id: string, updates: Partial<Omit<OutputField, 'id'>>) => {
    setOutputFields((prev) => prev.map((field) => (field.id === id ? { ...field, ...updates } : field)))
    setOutputFieldsError(null)
    setOutputConfigDirty(true)
    setConfigDirty(true)
  }, [])

  const removeOutputField = useCallback((id: string) => {
    setOutputFields((prev) => prev.filter((field) => field.id !== id))
    setOutputFieldsError(null)
    setOutputConfigDirty(true)
    setConfigDirty(true)
  }, [])

  const validateStructuredOutput = useCallback(() => {
    if (outputTypeSelection !== 'dict') {
      setOutputFieldsError(null)
      return null
    }
    if (outputFields.length === 0) {
      const message = 'Add at least one field for the JSON object output'
      setOutputFieldsError(message)
      throw new Error(message)
    }
    const usedNames = new Set<string>()
    for (const field of outputFields) {
      const name = field.name.trim()
      if (!name) {
        const message = 'Field names cannot be empty'
        setOutputFieldsError(message)
        throw new Error(message)
      }
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
        const message = 'Field names must start with a letter or underscore and contain only letters, numbers, or underscores'
        setOutputFieldsError(message)
        throw new Error(message)
      }
      if (usedNames.has(name)) {
        const message = 'Field names must be unique'
        setOutputFieldsError(message)
        throw new Error(message)
      }
      usedNames.add(name)
    }
    const builtSchema = fieldsToSchema(outputFields)
    if (!builtSchema) {
      const message = 'Define at least one valid field'
      setOutputFieldsError(message)
      throw new Error(message)
    }
    setOutputFieldsError(null)
    return builtSchema
  }, [outputFields, outputTypeSelection])

  const handleModelSettingChange = (field: ModelSettingDefinition, rawValue: string | boolean) => {
    setModelSettings((prev) => {
      const next = { ...prev }
      if (field.type === 'boolean') {
        if (rawValue) {
          next[field.key] = true
        } else {
          delete next[field.key]
        }
      } else if (field.type === 'enum') {
        if (typeof rawValue === 'string' && rawValue) {
          next[field.key] = rawValue
        } else {
          delete next[field.key]
        }
      } else {
        if (typeof rawValue !== 'string' || rawValue === '') {
          delete next[field.key]
        } else {
          const parsed = Number(rawValue)
          if (!Number.isNaN(parsed)) {
            next[field.key] = field.type === 'integer' ? Math.round(parsed) : parsed
          } else {
            delete next[field.key]
          }
        }
      }
      return next
    })
    setModelSettingsDirty(true)
    setConfigDirty(true)
  }

  const renderModelSettingField = (field: ModelSettingDefinition) => {
    const currentValue = modelSettings[field.key]
    if (field.type === 'boolean') {
      const checked = Boolean(currentValue)
      return (
        <div className="flex items-center gap-2">
          <input
            id={`model-setting-${field.key}`}
            type="checkbox"
            checked={checked}
            onChange={(event) => handleModelSettingChange(field, event.target.checked)}
            className="h-3 w-3 rounded border-input"
          />
          <span className="text-xs text-muted-foreground">{checked ? 'Enabled' : 'Disabled'}</span>
        </div>
      )
    }
    if (field.type === 'enum') {
      const value = typeof currentValue === 'string' ? currentValue : ''
      return (
        <select
          id={`model-setting-${field.key}`}
          value={value}
          onChange={(event) => handleModelSettingChange(field, event.target.value)}
          className="w-full rounded-md border border-border bg-background px-2 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
        >
          <option value="">Default</option>
          {(field.options ?? []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      )
    }
    let displayValue: string | number = ''
    if (typeof currentValue === 'number') {
      displayValue = currentValue
    } else if (typeof currentValue === 'string') {
      const parsed = Number(currentValue)
      if (!Number.isNaN(parsed)) {
        displayValue = parsed
      }
    }
    return (
      <input
        id={`model-setting-${field.key}`}
        type="number"
        value={displayValue}
        onChange={(event) => handleModelSettingChange(field, event.target.value)}
        min={field.min}
        max={field.max}
        step={field.step ?? (field.type === 'integer' ? 1 : 0.1)}
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
      />
    )
  }

  const handleSafeOptionToggle = (key: 'skip_approvals' | 'halt_on_rejection', value: boolean) => {
    setSafeOptions((prev) => ({ ...prev, [key]: value }))
    setSafeOptionsDirty(true)
    setConfigDirty(true)
  }

  const updateSafeToolName = (name: string, enabled: boolean) => {
    setSafeOptions((prev) => {
      const filtered = prev.safe_tool_names.filter((item) => item !== name)
      const nextNames = enabled ? [...filtered, name] : filtered
      return { ...prev, safe_tool_names: nextNames }
    })
    setSafeOptionsDirty(true)
    setConfigDirty(true)
  }

  const handleSafePatternChange = (value: string) => {
    setSafePatternInput(value)
    const patterns = value
      .split('\n')
      .map((line) => line.trim())
      .filter((line, index, self) => line.length > 0 && self.indexOf(line) === index)
    setSafeOptions((prev) => ({ ...prev, safe_tool_patterns: patterns }))
    setSafeOptionsDirty(true)
    setConfigDirty(true)
  }

  const toggleGuardrail = (type: 'input' | 'output', name: string) => {
    const available = type === 'input' ? availableInputGuardrails : availableOutputGuardrails
    if (!available.some((item) => item.name === name)) {
      return
    }
    if (type === 'input') {
      setSelectedInputGuardrails((prev) => (prev.includes(name) ? prev.filter((item) => item !== name) : [...prev, name]))
    } else {
      setSelectedOutputGuardrails((prev) => (prev.includes(name) ? prev.filter((item) => item !== name) : [...prev, name]))
    }
    setGuardrailsDirty(true)
    setConfigDirty(true)
  }

  function updateSelection(names: string[], markDirty = true) {
    const deduped = Array.from(new Set(names))
    setSelectedTools(deduped)
    setToolSelection(
      deduped.reduce<ToolSelection>((acc, key) => {
        acc[key] = true
        return acc
      }, {})
    )
    setSafeOptions((prev) => {
      const intersected = prev.safe_tool_names.filter((name) => deduped.includes(name))
      if (intersected.length !== prev.safe_tool_names.length) {
        if (markDirty) {
          setSafeOptionsDirty(true)
        }
        return {
          ...prev,
          safe_tool_names: intersected,
        }
      }
      return prev
    })
    if (markDirty) {
      setToolsDirty(true)
      setConfigDirty(true)
    }
    setFormError('')
  }

  const toggleTool = (name: string) => {
    updateSelection(
      toolSelection[name]
        ? selectedTools.filter((tool) => tool !== name)
        : [...selectedTools, name]
    )
  }

  const defaultModelHint = data?.default_model ? `e.g., ${data?.default_model}` : 'gpt-4o-mini'
  const selectedToolObjects = selectedTools
    .map((name) => tools.find((tool) => tool.name === name))
    .filter(Boolean) as PlaygroundToolDefinition[]

  const currentSnapshot = useMemo(
    () =>
      serializeAgentConfig(
        modelInput,
        instructions,
        selectedTools,
        modelSettings,
        useSafeAgent,
        safeOptions,
        selectedInputGuardrails,
        selectedOutputGuardrails,
        outputTypeSelection,
        outputTypeStrict,
        outputFields,
      ),
    [
      modelInput,
      instructions,
      selectedTools,
      modelSettings,
      useSafeAgent,
      safeOptions,
      selectedInputGuardrails,
      selectedOutputGuardrails,
      outputTypeSelection,
      outputTypeStrict,
      outputFields,
    ],
  )
  const currentComparisonString = useMemo(() => JSON.stringify(prepareComparison(currentSnapshot)), [currentSnapshot])
  const hasUnsavedDefaults = Boolean(agentKey) && initialComparison !== null && initialComparison !== currentComparisonString

  const handleResetConversation = useCallback(() => {
    resetConversation()
    setSessionId('')
    clearStatus()
  }, [resetConversation, clearStatus])

  const handleReset = () => {
    const defaultModel = data?.default_model ?? ''
    setModelInput(defaultModel)
    const shouldPrefill = Boolean(agentKey)
    if (shouldPrefill) {
      const defaultInstructions = data?.default_instructions ?? ''
      setInstructions(defaultInstructions)
      setInstructionsDirty(false)
      const defaultTools = Array.from(new Set(data?.default_tools ?? []))
      updateSelection(defaultTools, false)
      setToolsDirty(false)
      const defaultModelSettings = data?.default_model_settings ?? {}
      setModelSettings(defaultModelSettings)
      setModelSettingsDirty(false)
      const useSafeByDefault = Boolean(data?.safe_defaults?.use_safe_agent)
      setUseSafeAgent(useSafeByDefault)
      const resetSafeOptions = sanitizeSafeOptions(data?.safe_defaults?.options, defaultTools)
      setSafeOptions(resetSafeOptions)
      setSafeOptionsDirty(false)
      setSafePatternInput(resetSafeOptions.safe_tool_patterns.join('\n'))
      const defaultInputGuardrails = data?.guardrails?.default_input ?? []
      const defaultOutputGuardrails = data?.guardrails?.default_output ?? []
      setSelectedInputGuardrails(defaultInputGuardrails)
      setSelectedOutputGuardrails(defaultOutputGuardrails)
      setGuardrailsDirty(false)
      const defaultFields = schemaToFields(structuredOutputConfig?.default_schema)
      setOutputFields(defaultFields)
    } else {
      setInstructions('')
      setInstructionsDirty(false)
      updateSelection([], false)
      setToolsDirty(false)
      setModelSettings({})
      setModelSettingsDirty(false)
      const useSafeByDefault = Boolean(data?.safe_defaults?.use_safe_agent)
      setUseSafeAgent(useSafeByDefault)
      const resetSafeOptions = sanitizeSafeOptions(
        data?.safe_defaults?.options,
        tools.map((tool) => tool.name),
      )
      setSafeOptions(resetSafeOptions)
      setSafeOptionsDirty(false)
      setSafePatternInput(resetSafeOptions.safe_tool_patterns.join('\n'))
      setSelectedInputGuardrails([])
      setSelectedOutputGuardrails([])
      setGuardrailsDirty(false)
      setOutputFields([])
    }
    setOutputTypeSelection('default')
    setOutputTypeStrict(structuredOutputConfig ? structuredOutputConfig.default_strict ?? true : true)
    setOutputFieldsError(null)
    setOutputConfigDirty(false)
    setShowAdvanced(false)
    setSessionId('')
    setFormError('')
    setConfigDirty(false)
    setSessionStatus('')
    setStatusSpinner(false)
    setStatusSeverity('info')
    if (!isRunning) {
      clearStatus()
    }
  }

  const handleSaveDefaults = useCallback(async () => {
    if (!agentKey || agentUpdateMutation.isPending) {
      return
    }
    try {
      setFormError('')
      const trimmedModel = modelInput.trim()
      if (!trimmedModel) {
        const message = 'Enter a model name to continue'
        setFormError(message)
        applyStatus(message, { spinner: false, severity: 'error' })
        return
      }
      const schemaPayload = outputTypeSelection === 'dict' ? validateStructuredOutput() : null
      const payload: AgentUpdatePayload = {
        model: trimmedModel,
        instructions,
        tools: [...selectedTools],
        use_safe_agent: useSafeAgent,
        safe_agent_options: {
          skip_approvals: safeOptions.skip_approvals,
          halt_on_rejection: safeOptions.halt_on_rejection,
          safe_tool_names: [...safeOptions.safe_tool_names],
          safe_tool_patterns: [...safeOptions.safe_tool_patterns],
        },
        guardrails: {
          input: [...selectedInputGuardrails],
          output: [...selectedOutputGuardrails],
        },
      }
      if (Object.keys(modelSettings).length > 0) {
        payload.model_settings = modelSettings
      }
      if (outputTypeSelection !== 'default') {
        if (outputTypeSelection === 'none') {
          payload.output_type = 'none'
          payload.output_schema = null
        } else {
          payload.output_type = outputTypeSelection
          payload.output_type_strict = outputTypeStrict
          if (outputTypeSelection === 'dict') {
            payload.output_schema = schemaPayload ?? null
          } else {
            payload.output_schema = null
          }
        }
      }
      await agentUpdateMutation.mutateAsync(payload)
      setInitialComparison(currentComparisonString)
      setInstructionsDirty(false)
      setToolsDirty(false)
      setModelSettingsDirty(false)
      setSafeOptionsDirty(false)
      setGuardrailsDirty(false)
      setOutputConfigDirty(false)
      setConfigDirty(false)
      handleResetConversation()
      pushToast({ message: 'Agent updated', tone: 'success' })
      queryClient.invalidateQueries({ queryKey: metadataQueryKey as readonly unknown[] })
      queryClient.invalidateQueries({ queryKey: ['agents', 'list'] as const })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to save changes'
      setFormError(message)
      applyStatus(message, { spinner: false, severity: 'error' })
      pushToast({ message, tone: 'error' })
    }
  }, [
    agentKey,
    agentUpdateMutation,
    applyStatus,
    currentComparisonString,
    metadataQueryKey,
    modelInput,
    modelSettings,
    outputTypeSelection,
    outputTypeStrict,
    queryClient,
    safeOptions,
    selectedInputGuardrails,
    selectedOutputGuardrails,
    selectedTools,
    setFormError,
    pushToast,
    useSafeAgent,
    validateStructuredOutput,
    instructions,
    handleResetConversation,
  ])

  const ensureSession = async (trimmedModel: string) => {
    const hasSession = Boolean(sessionId)
    const shouldConfigure = !sessionId || configDirty
    const needsReconnect = configDirty && hasSession
    if (needsReconnect) {
      resetConversation()
    }
    if (shouldConfigure) {
      setFormError('')
      try {
        let schemaPayload: Record<string, unknown> | null = null
        if (outputConfigDirty && outputTypeSelection === 'dict') {
          schemaPayload = validateStructuredOutput()
        } else {
          setOutputFieldsError(null)
        }
        const payload: PlaygroundSessionPayload = {
          model: trimmedModel,
        }

        if (instructionsDirty) {
          payload.instructions = instructions
        }

        if (toolsDirty) {
          payload.tools = selectedTools
        }

        if (modelSettingsDirty && Object.keys(modelSettings).length > 0) {
          payload.model_settings = modelSettings
        }

        payload.use_safe_agent = useSafeAgent
        if (useSafeAgent || safeOptions.skip_approvals || !safeOptions.halt_on_rejection || safeOptions.safe_tool_names.length > 0 || safeOptions.safe_tool_patterns.length > 0) {
          payload.safe_agent_options = {
            skip_approvals: Boolean(safeOptions.skip_approvals),
            halt_on_rejection: Boolean(safeOptions.halt_on_rejection),
            safe_tool_names: [...safeOptions.safe_tool_names],
            safe_tool_patterns: [...safeOptions.safe_tool_patterns],
          }
        }

        if (guardrailsDirty) {
          payload.guardrails = {
            input: selectedInputGuardrails,
            output: selectedOutputGuardrails,
          }
        }

        if (outputConfigDirty) {
          if (outputTypeSelection !== 'default') {
            payload.output_type = outputTypeSelection
          }
          payload.output_type_strict = outputTypeStrict
          if (schemaPayload) {
            payload.output_schema = schemaPayload
          }
        }

        if (!needsReconnect && hasSession) {
          payload.session_id = sessionId
        }

        const response = await sessionMutation.mutateAsync(payload)
        setSessionId(response.session_id)
        if (!isRunning) {
          clearStatus()
        }
        setConfigDirty(false)
        setInstructionsDirty(false)
        setToolsDirty(false)
        setModelSettingsDirty(false)
        setSafeOptionsDirty(false)
        setGuardrailsDirty(false)
        setOutputConfigDirty(false)
        return response.session_id
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to save configuration'
        setFormError(message)
        throw new Error(message)
      }
    }
    return sessionId
  }

  const waitForConnection = useCallback(async () => {
    if (connectionStateRef.current === 'connected') {
      return
    }
    if (connectionStateRef.current === 'error') {
      throw new Error(connectionError || 'Connection error')
    }
    if (connectionStateRef.current === 'closed') {
      throw new Error('Connection closed')
    }
    await new Promise<void>((resolve, reject) => {
      const intervalId = window.setInterval(() => {
        const state = connectionStateRef.current
        if (state === 'connected') {
          window.clearInterval(intervalId)
          window.clearTimeout(timeoutId)
          resolve()
        } else if (state === 'error') {
          window.clearInterval(intervalId)
          window.clearTimeout(timeoutId)
          reject(new Error(connectionError || 'Connection error'))
        } else if (state === 'closed') {
          window.clearInterval(intervalId)
          window.clearTimeout(timeoutId)
          reject(new Error('Connection closed'))
        }
      }, 50)
      const timeoutId = window.setTimeout(() => {
        window.clearInterval(intervalId)
        reject(new Error('Timed out while connecting to playground'))
      }, 10000)
    })
  }, [connectionError])

  const handleSendPrompt = async (prompt: string) => {
    const trimmedPrompt = prompt.trim()
    if (!trimmedPrompt) {
      return
    }
    const trimmedModel = modelInput.trim()
    if (!trimmedModel) {
      const message = 'Enter a model name to continue'
      setFormError(message)
      throw new Error(message)
    }
    setFormError('')
    setIsSending(true)
    let attemptedConnection = false
    let attemptedRun = false
    try {
      await ensureSession(trimmedModel)
      applyStatus('Connecting…', { spinner: true, severity: 'info' })
      attemptedConnection = true
      await waitForConnection()
      applyStatus('Run in progress…', { spinner: true, severity: 'info' })
      attemptedRun = true
      await sendPrompt(trimmedPrompt)
    } catch (error) {
      const fallback = error instanceof Error ? error.message : 'Failed to send message'
      const message = connectionError && connectionStateRef.current === 'error'
        ? `Connection error: ${connectionError}`
        : fallback
      const shouldSurface = attemptedConnection || attemptedRun || connectionStateRef.current === 'error' || connectionStateRef.current === 'closed'
      if (shouldSurface) {
        applyStatus(message, { spinner: false, severity: 'error' })
      }
      if (connectionStateRef.current === 'error' || connectionStateRef.current === 'closed') {
        setSessionId('')
      }
      throw error instanceof Error ? error : new Error(fallback)
    } finally {
      setIsSending(false)
    }
  }

  useEffect(() => {
    if (!sessionId) {
      return
    }
    if (isRunning) {
      if (sessionStatus !== 'Run in progress…' || !statusSpinner) {
        applyStatus('Run in progress…', { spinner: true, severity: 'info' })
      }
      return
    }
    if (sessionStatus === 'Run in progress…') {
      clearStatus()
    }
  }, [applyStatus, clearStatus, isRunning, sessionId, sessionStatus, statusSpinner])

  const isPreparing = sessionMutation.isPending || isSending || connectionState === 'connecting'

  const outerPaddingClass = containerPadding ? 'p-6' : 'p-0'

  if (isLoading) {
    return (
      <div className={`h-full w-full ${outerPaddingClass}`}>
        <div className="mx-auto flex h-full max-w-4xl items-center justify-center text-sm text-muted-foreground">
          Loading playground configuration…
        </div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className={`h-full w-full ${outerPaddingClass}`}>
        <div className="mx-auto flex h-full max-w-4xl flex-col items-center justify-center gap-4 text-center">
          <p className="text-sm text-muted-foreground">Unable to load the playground metadata.</p>
          <button
            type="button"
            onClick={() => refetch()}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium hover:bg-muted"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!data?.available) {
    return (
      <div className={`h-full w-full ${outerPaddingClass}`}>
        <div className="mx-auto flex h-full max-w-4xl items-center justify-center text-center">
          <div className="space-y-3">
            <h2 className="text-xl font-semibold">Playground unavailable</h2>
            <p className="text-sm text-muted-foreground">
              Launch Studio from a project directory containing a manifest to enable the playground.
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={`h-full w-full overflow-y-auto ${outerPaddingClass} lg:overflow-hidden`}>
      <div className="mx-auto flex h-full max-w-6xl flex-col gap-6 lg:grid lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
        <section className="flex min-h-0 flex-col rounded-md border border-border bg-background">
          <div className="flex-1 space-y-6 overflow-y-auto p-5">
            <header className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-xl font-semibold">{heading}</h2>
              <div className="flex items-center gap-2">
                {agentKey ? (
                  <button
                    type="button"
                    onClick={handleSaveDefaults}
                    disabled={!hasUnsavedDefaults || agentUpdateMutation.isPending || isLoading}
                    className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {agentUpdateMutation.isPending ? 'Saving…' : 'Save changes'}
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={handleReset}
                  className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
                >
                  Reset
                </button>
              </div>
            </header>

            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="playground-model">Model</label>
              <input
                id="playground-model"
                value={modelInput}
                onChange={(event) => {
                  const value = event.target.value
                  setModelInput(value)
                  setConfigDirty(true)
                  setFormError('')
                }}
                placeholder={defaultModelHint}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
              {formError && <p className="text-xs text-destructive">{formError}</p>}
            </div>

            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-medium">Tools</h3>
                  <p className="text-xs text-muted-foreground">Selected tools are shown below. Add or remove tools as needed.</p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setToolModalOpen(true)}
                    className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
                  >
                    Add tools
                  </button>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 rounded-md border border-dashed border-border p-3">
                {selectedToolObjects.length === 0 && (
                  <span className="text-xs text-muted-foreground">No tools selected.</span>
                )}
                {selectedToolObjects.map((tool) => (
                  <button
                    key={tool.name}
                    type="button"
                    onClick={() => toggleTool(tool.name)}
                    className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-3 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                  >
                    <span>{tool.name}</span>
                    <span className="font-semibold">×</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="playground-instructions">Instructions</label>
              <textarea
                id="playground-instructions"
                value={instructions}
                onChange={(event) => {
                  setInstructions(event.target.value)
                  setInstructionsDirty(true)
                  setConfigDirty(true)
                  setFormError('')
                }}
                rows={10}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>

            <div className="space-y-2">
              <button
                type="button"
                onClick={() => setShowAdvanced((prev) => !prev)}
                className="flex w-full items-center justify-between rounded-md border border-border bg-background px-3 py-2 text-sm font-medium hover:bg-muted"
              >
                <span>Model parameters</span>
                {showAdvanced ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
              </button>
              {showAdvanced && (
                <div className="space-y-3 rounded-md border border-border bg-muted/20 p-3">
                  {modelSettingsSchema.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No adjustable model parameters available.</p>
                  ) : (
                    modelSettingsSchema.map((field) => (
                      <div key={field.key} className="space-y-1">
                        <label className="text-xs font-medium text-muted-foreground" htmlFor={`model-setting-${field.key}`}>
                          {field.label}
                        </label>
                        {renderModelSettingField(field)}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>

            {structuredOutputConfig && (
              <div className="space-y-3 rounded-md border border-border bg-muted/10 p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium">Structured output</h3>
                  <span className="text-xs text-muted-foreground">Default: {structuredOutputConfig.default_label}</span>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground" htmlFor="structured-output-format">
                    Output format
                  </label>
                  <select
                    id="structured-output-format"
                    value={outputTypeSelection}
                    onChange={(event) => handleOutputTypeChange(event.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                  >
                    {structuredOutputOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={outputTypeStrict}
                    onChange={(event) => {
                      setOutputTypeStrict(event.target.checked)
                      setOutputConfigDirty(true)
                      setConfigDirty(true)
                    }}
                    className="h-3 w-3 rounded border-input"
                    disabled={outputTypeSelection === 'none'}
                  />
              Strict JSON schema
                </label>
                {outputTypeSelection === 'dict' && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-muted-foreground">Fields</span>
                      <button
                        type="button"
                        onClick={addOutputField}
                        className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] font-medium hover:bg-muted"
                      >
                        <Plus className="h-3 w-3" />
                        Add field
                      </button>
                    </div>
                    <div className="space-y-2">
                      {outputFields.length === 0 && (
                        <p className="rounded-md border border-dashed border-border bg-muted/20 p-3 text-xs text-muted-foreground">
                          Add fields to define the JSON object your model should return.
                        </p>
                      )}
                      {outputFields.map((field) => (
                        <div key={field.id} className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-background p-3">
                          <div className="flex min-w-[140px] flex-1 items-center gap-2">
                            <label className="text-xs text-muted-foreground" htmlFor={`field-name-${field.id}`}>
                              Name
                            </label>
                            <input
                              id={`field-name-${field.id}`}
                              value={field.name}
                              onChange={(event) => updateOutputField(field.id, { name: event.target.value })}
                              placeholder="result"
                              className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                            />
                          </div>
                          <div className="flex min-w-[140px] flex-1 items-center gap-2">
                            <label className="text-xs text-muted-foreground" htmlFor={`field-type-${field.id}`}>
                              Type
                            </label>
                            <select
                              id={`field-type-${field.id}`}
                              value={field.type}
                              onChange={(event) => updateOutputField(field.id, { type: event.target.value as OutputFieldType })}
                              className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                            >
                              {OUTPUT_FIELD_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </div>
                          <label className="flex items-center gap-2 text-xs text-muted-foreground">
                            <input
                              type="checkbox"
                              checked={field.required}
                              onChange={(event) => updateOutputField(field.id, { required: event.target.checked })}
                              className="h-3 w-3 rounded border-input"
                            />
                            Required
                          </label>
                          <button
                            type="button"
                            onClick={() => removeOutputField(field.id)}
                            className="inline-flex items-center justify-center rounded-md border border-border bg-muted/40 p-1 text-muted-foreground hover:bg-muted"
                            aria-label="Remove field"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                    {outputFieldsError && <p className="text-xs text-destructive">{outputFieldsError}</p>}
                    {schemaPreview && (
                      <div className="rounded-md border border-dashed border-border bg-background p-3">
                        <p className="mb-2 text-[11px] font-medium text-muted-foreground">Generated schema preview</p>
                        <pre className="whitespace-pre-wrap break-all text-[11px] leading-tight text-muted-foreground">
                          {schemaPreview}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
                {outputTypeSelection === 'default' && defaultSchemaPreview && (
                  <div className="rounded-md border border-dashed border-border bg-background p-3">
                    <pre className="whitespace-pre-wrap break-all text-[11px] leading-tight text-muted-foreground">
                      {defaultSchemaPreview}
                    </pre>
                  </div>
                )}
              </div>
            )}

            <div className="rounded-md border border-border bg-muted/10 p-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Safety</h3>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={useSafeAgent}
                    onChange={(event) => {
                      setUseSafeAgent(event.target.checked)
                      setConfigDirty(true)
                    }}
                    className="h-3 w-3 rounded border-input"
                  />
                  Use SafeAgent
                </label>
              </div>
              {useSafeAgent && (
                <div className="mt-4 space-y-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="flex items-center gap-2 text-xs text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={safeOptions.skip_approvals}
                        onChange={(event) => handleSafeOptionToggle('skip_approvals', event.target.checked)}
                        className="h-3 w-3 rounded border-input"
                        disabled={!useSafeAgent}
                      />
                      Skip approvals
                    </label>
                    <label className="flex items-center gap-2 text-xs text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={safeOptions.halt_on_rejection}
                        onChange={(event) => handleSafeOptionToggle('halt_on_rejection', event.target.checked)}
                        className="h-3 w-3 rounded border-input"
                        disabled={!useSafeAgent}
                      />
                      Halt on rejection
                    </label>
                  </div>
                  <div className="space-y-1">
                    <div className="text-xs font-medium text-muted-foreground">Safe tool names</div>
                    <div className="space-y-2 rounded-md border border-dashed border-border p-2">
                      {selectedTools.length === 0 ? (
                        <span className="text-xs text-muted-foreground">No tools selected.</span>
                      ) : (
                        selectedTools.map((toolName) => {
                          const isMarkedSafe = safeOptions.safe_tool_names.includes(toolName)
                          const requiresApproval = !isMarkedSafe
                          return (
                            <label
                              key={toolName}
                              className={`flex items-center justify-between rounded-md border px-3 py-2 text-xs ${
                                useSafeAgent ? 'hover:border-primary hover:bg-primary/5' : 'opacity-60'
                              }`}
                            >
                              <span className="font-medium text-foreground">{toolName}</span>
                              <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
                                <span>Requires approval</span>
                                <input
                                  type="checkbox"
                                  checked={requiresApproval}
                                  onChange={(event) => updateSafeToolName(toolName, !event.target.checked)}
                                  disabled={!useSafeAgent}
                                  className="h-3 w-3 rounded border-input"
                                />
                              </span>
                            </label>
                          )
                        })
                      )}
                    </div>
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-muted-foreground" htmlFor="safe-tool-patterns">
                      Safe tool patterns
                    </label>
                    <textarea
                      id="safe-tool-patterns"
                      rows={3}
                      value={safePatternInput}
                      onChange={(event) => handleSafePatternChange(event.target.value)}
                      disabled={!useSafeAgent}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
                    />
                  </div>
                </div>
              )}
            </div>

            <div className="space-y-3 rounded-md border border-border bg-muted/10 p-4">
              <h3 className="text-sm font-medium">Guardrails</h3>
              <div className="space-y-3">
                <div className="space-y-1">
                  <div className="text-xs font-medium text-muted-foreground">Input guardrails</div>
                  {availableInputGuardrails.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No input guardrails discovered.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {availableInputGuardrails.map((guardrail) => {
                        const active = selectedInputGuardrails.includes(guardrail.name)
                        return (
                          <button
                            key={guardrail.name}
                            type="button"
                            onClick={() => toggleGuardrail('input', guardrail.name)}
                            className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                              active ? 'border-primary bg-primary/10 text-primary' : 'border-border bg-muted text-muted-foreground'
                            } hover:bg-primary/20 hover:text-primary`}
                          >
                            {guardrail.label}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
                <div className="space-y-1">
                  <div className="text-xs font-medium text-muted-foreground">Output guardrails</div>
                  {availableOutputGuardrails.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No output guardrails discovered.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {availableOutputGuardrails.map((guardrail) => {
                        const active = selectedOutputGuardrails.includes(guardrail.name)
                        return (
                          <button
                            key={guardrail.name}
                            type="button"
                            onClick={() => toggleGuardrail('output', guardrail.name)}
                            className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                              active ? 'border-primary bg-primary/10 text-primary' : 'border-border bg-muted text-muted-foreground'
                            } hover:bg-primary/20 hover:text-primary`}
                          >
                            {guardrail.label}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="flex min-h-0 flex-col rounded-md border border-border bg-background">
          <div className="flex flex-1 flex-col overflow-hidden p-4">
            <PlaygroundChatPanel
              events={events}
              usageTotals={usageTotals}
              connectionState={connectionState}
              connectionError={connectionError}
              isRunning={isRunning}
              onSend={handleSendPrompt}
              onStop={stopRun}
              onApprovalDecision={respondToApproval}
              sessionId={sessionId}
              isPreparing={isPreparing}
              statusText={sessionStatus}
              statusSpinner={statusSpinner}
              statusSeverity={statusSeverity}
              onResetConversation={handleResetConversation}
            />
          </div>
        </section>
      </div>

      {toolModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="max-h-[80vh] w-full max-w-3xl overflow-hidden rounded-lg border border-border bg-background shadow-xl">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <h3 className="text-sm font-semibold">Select tools</h3>
              <button
                type="button"
                onClick={() => setToolModalOpen(false)}
                className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
              >
                Close
              </button>
            </div>
            <div className="max-h-[60vh] overflow-y-auto px-4 py-4">
              {tools.length === 0 && (
                <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
                  No tools discovered in this project.
                </div>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                {tools.map((tool) => {
                  const isSelected = !!toolSelection[tool.name]
                  return (
                    <button
                      key={tool.name}
                      type="button"
                      onClick={() => toggleTool(tool.name)}
                      className={`flex h-full flex-col rounded-md border px-3 py-3 text-left text-sm transition-shadow hover:shadow ${
                        isSelected ? 'border-primary shadow-sm' : 'border-border'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">{tool.name}</span>
                        <span
                          className={`h-2.5 w-2.5 rounded-full ${isSelected ? 'bg-primary' : 'bg-border'}`}
                        />
                      </div>
                      {tool.description && (
                        <p className="mt-2 text-xs text-muted-foreground">{tool.description}</p>
                      )}
                    </button>
                  )
                })}
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
              <button
                type="button"
                onClick={() => {
                  updateSelection([])
                }}
                className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted"
              >
                Clear all
              </button>
              <button
                type="button"
                onClick={() => setToolModalOpen(false)}
                className="rounded-md bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export function PlaygroundView({ onUpdateContext }: PlaygroundViewProps) {
  return (
    <AgentWorkbench
      key="playground"
      metadataQueryKey={['playground', 'metadata']}
      metadataFetcher={() => api.getPlaygroundMetadata()}
      configureSession={(payload) => api.createPlaygroundSession(payload)}
      onUpdateContext={onUpdateContext}
      heading="Configure your playground agent"
    />
  )
}

type ApprovalAction = 'approve' | 'reject' | 'always'

interface PlaygroundApprovalRequestCardProps {
  event: PlaygroundChatEvent & { approvalId: string }
  pendingAction?: ApprovalAction
  disabled: boolean
  error?: string
  onDecision: (action: ApprovalAction) => void
}

function PlaygroundApprovalRequestCard({ event, pendingAction, disabled, error, onDecision }: PlaygroundApprovalRequestCardProps) {
  const toolName = event.name || 'tool'
  const argsText = event.arguments && event.arguments.trim().length > 0 ? event.arguments : 'No parameters'
  const content = renderApprovalDetails(event.metadata, argsText)
  const rejectLabel = pendingAction === 'reject' ? 'Rejecting…' : 'Reject'
  const alwaysLabel = pendingAction === 'always' ? 'Saving…' : 'Always'
  const approveLabel = pendingAction === 'approve' ? 'Approving…' : 'Approve once'
  // Hosted-MCP approvals ride the same approval card but are labeled
  // "MCP Tool Approval", show the MCP server_label, and intentionally
  // omit the "Always" button (no SDK-level always-allow on this path).
  const isMcp = event.kind === 'mcp'
  const heading = isMcp ? `MCP Tool Approval: ${toolName}` : `Approve ${toolName}`
  return (
    <div className="mr-auto inline-block max-w-[80%]">
      <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 shadow-sm">
        <div className="flex items-start gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-full border border-amber-500 text-amber-600">
            <ShieldCheck className="h-4 w-4" />
          </div>
          <div className="flex-1 space-y-3">
            <div className="space-y-1">
              <div className="text-sm font-medium text-foreground">{heading}</div>
              {isMcp && event.serverLabel && (
                <div className="text-xs text-muted-foreground">
                  Server: <span className="font-mono">{event.serverLabel}</span>
                </div>
              )}
              {content}
            </div>
            {error && <div className="text-xs text-destructive whitespace-pre-wrap">{error}</div>}
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-destructive transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
                onClick={() => onDecision('reject')}
                disabled={disabled}
              >
                {rejectLabel}
              </button>
              {!isMcp && (
                <button
                  type="button"
                  className="rounded-md border border-amber-500/60 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-600 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-60"
                  onClick={() => onDecision('always')}
                  disabled={disabled}
                >
                  {alwaysLabel}
                </button>
              )}
              <button
                type="button"
                className="rounded-md bg-amber-500 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-amber-500/90 disabled:cursor-not-allowed disabled:opacity-70"
                onClick={() => onDecision('approve')}
                disabled={disabled}
              >
                {isMcp ? (pendingAction === 'approve' ? 'Approving…' : 'Approve') : approveLabel}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function renderApprovalDetails(metadata: Record<string, unknown> | undefined, args: string): JSX.Element {
  const fallback = args.trim().length > 0 ? args : 'No parameters'
  const displayTypeValue = metadata ? metadata['display_type'] : undefined
  const displayType = typeof displayTypeValue === 'string' ? displayTypeValue : undefined
  if (displayType === 'diff') {
    const valueCandidate = metadata ? metadata['value'] : undefined
    const valueObject = valueCandidate && typeof valueCandidate === 'object' ? (valueCandidate as Record<string, unknown>) : undefined
    const diffRaw = valueObject ? valueObject['diff_lines'] : undefined
    const lines = Array.isArray(diffRaw) ? diffRaw.filter((line): line is string => typeof line === 'string') : []
    if (lines.length > 0) {
      return <DiffView lines={lines} />
    }
    return <p className="text-xs text-muted-foreground whitespace-pre-wrap">{fallback}</p>
  }
  if (displayType === 'file_write') {
    const valueCandidate = metadata ? metadata['value'] : undefined
    const text = typeof valueCandidate === 'string' && valueCandidate.trim().length > 0 ? valueCandidate : fallback
    return (
      <pre className="whitespace-pre-wrap break-words rounded-md border border-border bg-background px-3 py-2 text-xs font-mono">
        {text}
      </pre>
    )
  }
  if (displayType === 'command') {
    const metaCandidate = metadata ? metadata['metadata'] : undefined
    const metaSection = metaCandidate && typeof metaCandidate === 'object' ? (metaCandidate as Record<string, unknown>) : undefined
    const commandValue = metaSection ? metaSection['command'] : undefined
    const commandText = typeof commandValue === 'string' && commandValue.trim().length > 0 ? commandValue : fallback
    return <code className="rounded-md bg-muted px-2 py-1 text-xs font-mono">{commandText}</code>
  }
  const previewValue = metadata ? metadata['preview'] : undefined
  if (typeof previewValue === 'string' && previewValue.trim().length > 0) {
    return <p className="text-xs text-muted-foreground whitespace-pre-wrap">{previewValue}</p>
  }
  return <p className="text-xs text-muted-foreground whitespace-pre-wrap">{fallback}</p>
}

function DiffView({ lines }: { lines: string[] }): JSX.Element {
  return (
    <pre className="whitespace-pre-wrap break-words rounded-md border border-border bg-background px-3 py-2 text-xs font-mono">
      {lines.map((line, index) => {
        const toneClass = line.startsWith('+')
          ? 'text-emerald-500'
          : line.startsWith('-')
            ? 'text-destructive'
            : line.startsWith('@')
              ? 'text-blue-500'
              : 'text-muted-foreground'
        return (
          <span key={`${index}-${line}`} className={`${toneClass} block`}>
            {line}
          </span>
        )
      })}
    </pre>
  )
}

interface PlaygroundChatPanelProps {
  events: PlaygroundChatEvent[]
  usageTotals: PlaygroundUsageTotals | null
  connectionState: PlaygroundConnectionState
  connectionError: string | null
  isRunning: boolean
  onSend: (prompt: string) => Promise<void>
  onStop: () => void
  onApprovalDecision: (approvalId: string, decision: PlaygroundApprovalDecision) => Promise<void>
  sessionId: string
  isPreparing: boolean
  statusText: string
  statusSpinner: boolean
  statusSeverity: 'info' | 'error'
  onResetConversation: () => void
}

function PlaygroundChatPanel({
  events,
  usageTotals,
  connectionState,
  connectionError,
  isRunning,
  onSend,
  onStop,
  onApprovalDecision,
  sessionId,
  isPreparing,
  statusText,
  statusSpinner,
  statusSeverity,
  onResetConversation,
}: PlaygroundChatPanelProps) {
  const [input, setInput] = useState('')
  const [composerError, setComposerError] = useState('')
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const [approvalPending, setApprovalPending] = useState<Record<string, 'approve' | 'reject' | 'always'>>({})
  const [approvalErrors, setApprovalErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    const el = messagesRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight })
  }, [events])

  const handleSend = async () => {
    const trimmed = input.trim()
    if (!trimmed) {
      return
    }
    setComposerError('')
    try {
      await onSend(trimmed)
      setInput('')
    } catch (error) {
      if (error instanceof Error) {
        setComposerError(error.message)
      } else {
        setComposerError('Failed to send message')
      }
    }
  }

  const handleApproval = async (
    approvalId: string,
    action: 'approve' | 'reject' | 'always',
    decision: PlaygroundApprovalDecision
  ) => {
    setApprovalErrors((prev) => {
      const next = { ...prev }
      delete next[approvalId]
      return next
    })
    setApprovalPending((prev) => ({ ...prev, [approvalId]: action }))
    try {
      await onApprovalDecision(approvalId, decision)
      setApprovalPending((prev) => {
        const next = { ...prev }
        delete next[approvalId]
        return next
      })
      setApprovalErrors((prev) => {
        const next = { ...prev }
        delete next[approvalId]
        return next
      })
    } catch (error) {
      setApprovalPending((prev) => {
        const next = { ...prev }
        delete next[approvalId]
        return next
      })
      const message = error instanceof Error ? error.message : 'Failed to send approval response'
      setApprovalErrors((prev) => ({ ...prev, [approvalId]: message }))
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault()
      handleSend()
    }
  }

  const canSend = !isPreparing && !isRunning

  const handleResetClick = () => {
    setInput('')
    setComposerError('')
    setApprovalPending({})
    setApprovalErrors({})
    onResetConversation()
  }

  const connectionStatus = (() => {
    if (isPreparing) {
      return { label: 'Preparing…', dot: 'bg-amber-500', tone: 'text-amber-600 dark:text-amber-400' }
    }
    switch (connectionState) {
      case 'connected':
        return { label: 'Connected', dot: 'bg-emerald-500', tone: 'text-emerald-600 dark:text-emerald-400' }
      case 'connecting':
        return { label: 'Connecting…', dot: 'bg-amber-500', tone: 'text-amber-600 dark:text-amber-400' }
      case 'error':
        return { label: 'Error', dot: 'bg-destructive', tone: 'text-destructive' }
      case 'closed':
        return { label: sessionId ? 'Disconnected' : 'Idle', dot: 'bg-muted-foreground/60', tone: 'text-muted-foreground' }
      case 'idle':
      default:
        return { label: sessionId ? 'Idle' : 'Not connected', dot: 'bg-muted-foreground/60', tone: 'text-muted-foreground' }
    }
  })()

  const composerPlaceholder = 'Type your message…'

  const connectionBadge = (
    <span
      className={`inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] font-medium ${connectionStatus.tone}`}
      title={sessionId ? `Session ID: ${sessionId}` : undefined}
    >
      <span className={`h-2 w-2 rounded-full ${connectionStatus.dot}`} />
      <span>{connectionStatus.label}</span>
    </span>
  )

  const statusRow = statusText
    ? (
        <div className={`flex items-start gap-2 text-xs leading-relaxed ${statusSeverity === 'error' ? 'text-destructive' : 'text-muted-foreground'}`}>
          {statusSpinner ? (
            <span className="mt-0.5 inline-flex h-3 w-3 animate-spin rounded-full border-[1.5px] border-primary border-t-transparent" />
          ) : (
            <span className="mt-1 inline-flex h-2 w-2 rounded-full bg-muted-foreground/60" />
          )}
          <span className="whitespace-pre-wrap">{statusText}</span>
        </div>
      )
    : null

  const eventNodes = events.map((event) => {
    if (event.type === 'user') {
      return (
        <div
          key={event.id}
          className="ml-auto inline-block max-w-[70%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground shadow"
          style={{ whiteSpace: 'pre-wrap' }}
        >
          {event.content}
        </div>
      )
    }
    if (event.type === 'assistant') {
      return (
        <div
          key={event.id}
          className="mr-auto inline-block max-w-[70%] rounded-lg border border-border bg-background px-3 py-2 text-sm shadow"
        >
          <MarkdownMessage content={event.content || ''} variant="assistant" />
        </div>
      )
    }
    if (event.type === 'tool') {
      return (
        <div key={event.id} className="mr-auto inline-block max-w-[80%] rounded-lg border border-primary/40 bg-primary/5 px-3 py-2 text-xs text-primary">
          <div className="font-medium">Tool: {event.name}</div>
          {event.input && (
            <div className="mt-1 text-[11px] text-primary/80" style={{ whiteSpace: 'pre-wrap' }}>
              Input: {event.input}
            </div>
          )}
          {event.status === 'completed' && event.output && (
            <div className="mt-1 text-[11px] text-primary/80" style={{ whiteSpace: 'pre-wrap' }}>
              Output: {event.output}
            </div>
          )}
          <div className="mt-1 text-[10px] uppercase tracking-wide text-primary/60">
            {event.status === 'completed' ? 'Completed' : 'Called'}
          </div>
        </div>
      )
    }
    if (event.type === 'approval') {
      if (!event.approvalId) {
        return null
      }
      const pendingAction = approvalPending[event.approvalId]
      const error = approvalErrors[event.approvalId]
      const isPending = Boolean(pendingAction)
      return (
        <PlaygroundApprovalRequestCard
          key={event.id}
          event={event as PlaygroundChatEvent & { approvalId: string }}
          pendingAction={pendingAction}
          disabled={isPending}
          error={error}
          onDecision={(action) => {
            if (!event.approvalId) {
              return
            }
            if (action === 'approve') {
              handleApproval(event.approvalId, 'approve', { approved: true, toolName: event.name })
            } else if (action === 'always') {
              handleApproval(event.approvalId, 'always', { approved: true, alwaysApprove: true, toolName: event.name })
            } else {
              handleApproval(event.approvalId, 'reject', { approved: false, toolName: event.name })
            }
          }}
        />
      )
    }
    return (
      <div
        key={event.id}
        className={`text-[11px] ${event.severity === 'error' ? 'text-destructive' : 'text-muted-foreground'}`}
        style={{ whiteSpace: 'pre-wrap' }}
      >
        {event.content}
      </div>
    )
  })

  const messageContent = events.length === 0
    ? [
        <div key="empty" className="flex flex-1 items-center justify-center text-center text-xs text-muted-foreground">
          Send a message to begin. Your first message will configure the session automatically.
        </div>,
      ]
    : [...eventNodes]

  if (statusRow) {
    messageContent.push(
      <div key="status" className="pt-1">
        {statusRow}
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {connectionBadge}
          {connectionError && (
            <span className="text-destructive">{connectionError}</span>
          )}
        </div>
        {usageTotals && (
          <div className="text-xs text-muted-foreground">
            Usage · Requests: {usageTotals.requests} · Input: {usageTotals.input_tokens} · Output: {usageTotals.output_tokens} · Total: {usageTotals.total_tokens}
          </div>
        )}
      </div>

      <div
        ref={messagesRef}
        className="flex-1 min-h-[260px] overflow-y-auto rounded-md border border-border bg-muted/20 p-3"
      >
        <div className="flex min-h-full flex-col gap-3">
          {messageContent}
        </div>
      </div>

      <div className="space-y-2">
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          rows={3}
          placeholder={composerPlaceholder}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-70"
          disabled={isRunning || isPreparing}
        />
        {composerError && <p className="text-xs text-destructive">{composerError}</p>}
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={handleResetClick}
            disabled={isRunning || isPreparing}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
          >
            Reset chat
          </button>
          <button
            type="button"
            onClick={onStop}
            disabled={!isRunning}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
          >
            Stop
          </button>
          <button
            type="button"
            onClick={handleSend}
            disabled={!input.trim() || !canSend}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-70"
          >
            Send
          </button>
        </div>
        <p className="text-[11px] text-muted-foreground">Press Ctrl+Enter (or ⌘+Enter) to send.</p>
      </div>
    </div>
  )
}
