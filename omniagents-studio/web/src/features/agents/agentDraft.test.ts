import { describe, expect, it } from 'vitest'

import type { AgentSource } from '../../types/evaluation'
import { changesOf, draftOf } from './agentDraft'

const source: AgentSource = {
  key: 'main',
  path: '/p/agents/main/agent.yml',
  yaml: '',
  instructions: 'Help.',
  instructions_path: '/p/agents/main/instructions.md',
  error: null,
  settings: {
    name: 'main',
    model: 'gpt-5.2',
    capabilities: ['filesystem'],
    safe_agent_options: { skip_approvals: false, safe_tool_names: ['read_file'] },
    model_settings: { temperature: 0.2 },
    handoffs: [{ agent: 'helper' }],
  },
}

describe('changesOf', () => {
  it('sends only what the form changed', () => {
    const original = draftOf(source)
    const { changes, problems } = changesOf({ ...original, model: 'gpt-5.5' }, original, source.settings!)
    expect(problems).toEqual([])
    expect(changes).toEqual({ model: 'gpt-5.5' })
  })

  it('removes a setting cleared in the form and keeps safe-agent options it does not show', () => {
    const original = draftOf(source)
    const { changes } = changesOf(
      { ...original, capabilities: [], safe_tool_names: ['read_file', 'list_directory'] },
      original,
      source.settings!,
    )
    expect(changes).toEqual({
      capabilities: null,
      safe_agent_options: {
        skip_approvals: false,
        safe_tool_names: ['read_file', 'list_directory'],
        halt_on_rejection: true,
      },
    })
  })

  it('refuses JSON fields that do not parse', () => {
    const original = draftOf(source)
    const { problems } = changesOf({ ...original, model_settings: '{temperature' }, original, source.settings!)
    expect(problems).toEqual(['Model settings must be JSON'])
  })
})
