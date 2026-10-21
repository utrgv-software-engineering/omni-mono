import React, { useCallback, useEffect, useState } from 'react'
import type { ModelDescriptor } from '../protocol/catalog'
import type { RPCClient } from '../rpc/client'

/**
 * Header model picker backed by the typed model-catalog RPCs
 * (`list_models` / `set_session_model`) — the same provider-neutral
 * catalog contract every maintained client consumes.
 *
 * Unavailable models stay selectable-looking but disabled, with their
 * first typed availability reason as the tooltip; deprecated models are
 * annotated with their upgrade guidance.
 */
export function ModelPicker({
  client,
  sessionId,
  currentModel,
}: {
  client: RPCClient
  sessionId?: string
  /** Catalog identity from `ui.set_session_state`, when known. */
  currentModel?: string
}) {
  const [models, setModels] = useState<ModelDescriptor[]>([])
  const [selected, setSelected] = useState<string>('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const catalog = await client.listModels({ sessionId, modality: 'text' })
        if (cancelled) return
        setModels(catalog.models.filter(m => !m.realtime))
        const active = catalog.session?.active_model || catalog.default_model
        if (active) setSelected(active)
      } catch {
        // Catalog unavailable (e.g. no model store) — render nothing.
      }
    })()
    return () => { cancelled = true }
  }, [client, sessionId])

  useEffect(() => {
    if (currentModel) setSelected(currentModel)
  }, [currentModel])

  const onChange = useCallback(async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const id = e.target.value
    if (!sessionId || !id) return
    const previous = selected
    setSelected(id)
    setBusy(true)
    try {
      const result = await client.setSessionModel(sessionId, id)
      if (!result.ok) setSelected(previous)
    } catch {
      setSelected(previous)
    } finally {
      setBusy(false)
    }
  }, [client, sessionId, selected])

  if (!models.length) return null

  return (
    <select
      className="h-8 max-w-48 rounded bg-bgCardAlt text-textPrimary text-xs px-2 disabled:opacity-50"
      value={selected}
      onChange={onChange}
      disabled={busy || !sessionId}
      aria-label="Model"
      title={sessionId ? 'Switch model for this session' : 'Start a chat to switch models'}
    >
      {models.map(m => {
        const reason = m.availability.reasons[0]
        const deprecated = m.deprecation.deprecated
        const label = [
          m.label,
          m.is_default ? '(default)' : '',
          deprecated ? (m.deprecation.replace_with ? `(deprecated → ${m.deprecation.replace_with})` : '(deprecated)') : '',
        ].filter(Boolean).join(' ')
        return (
          <option
            key={m.id}
            value={m.id}
            disabled={!m.availability.available}
            title={reason ? `${reason.message}${reason.action ? ` ${reason.action}` : ''}` : m.description || undefined}
          >
            {label}
          </option>
        )
      })}
    </select>
  )
}
