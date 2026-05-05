import React, { useMemo, useState } from 'react'
import type { Span } from '../../../types'
import { cn } from '../../../lib/utils'
import {
  User,
  Bot,
  Wrench,
  ArrowRight,
  Shield,
  Mic,
  Volume2,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Activity
} from 'lucide-react'
import { useTrace } from '../../traces/hooks/useTraces'
import { useTraceStore } from '../../traces/store/traceStore'
import { MarkdownMessage } from '../../../shared/components/MarkdownMessage'

interface ConversationFlowProps {
  spans: Span[]
}

interface ParsedMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: string
  spanId: string
  error?: any
  tokens?: {
    input?: number
    output?: number
  }
}

interface ParsedFunction {
  name: string
  input: any
  output: any
  timestamp: string
  spanId: string
  error?: any
  isVerbose?: boolean
  callId?: string
}

interface ParsedHandoff {
  from: string
  to: string
  timestamp: string
  spanId: string
}

interface ParsedGuardrail {
  name: string
  triggered: boolean
  timestamp: string
  spanId: string
}

interface ParsedVoice {
  type: 'transcription' | 'speech'
  text: string
  timestamp: string
  spanId: string
}

type ParsedItem = 
  | { type: 'message'; data: ParsedMessage }
  | { type: 'function'; data: ParsedFunction }
  | { type: 'handoff'; data: ParsedHandoff }
  | { type: 'guardrail'; data: ParsedGuardrail }
  | { type: 'voice'; data: ParsedVoice }
  | { type: 'agent'; data: { name: string; timestamp: string; spanId: string } }

// Safe JSON parsing helper that returns the original value if parsing fails
function safeJsonParse(value: any): any {
  if (typeof value !== 'string') {
    return value
  }
  
  // If it's an empty string or doesn't look like JSON, return as-is
  if (!value.trim() || (!value.trim().startsWith('{') && !value.trim().startsWith('['))) {
    return value
  }
  
  try {
    return JSON.parse(value)
  } catch {
    // If parsing fails, return the original string
    return value
  }
}

export function ConversationFlow({ spans }: ConversationFlowProps) {
  const [expandedFunctions, setExpandedFunctions] = React.useState<Set<string>>(new Set())
  const [showSystemMessages, setShowSystemMessages] = React.useState(false)
  const [showMetadata, setShowMetadata] = React.useState(false)
  const conversationRef = React.useRef<HTMLDivElement>(null)
  
  // Get the trace ID from the first span (all spans in a trace share the same trace_id)
  const traceId = spans.length > 0 ? spans[0].trace_id : null
  const { selectedProject } = useTraceStore()
  const { data: traceData } = useTrace(selectedProject, traceId)

  const syntheticBadges = useMemo(() => {
    const md = traceData?.trace?.metadata
    let meta: any = null
    if (md) {
      if (typeof md === 'string') {
        try { meta = JSON.parse(md) } catch { meta = null }
      } else {
        meta = md
      }
    }
    const oa = meta?.omniagents
    if (!oa || oa.run_type !== 'synthetic_generation') return [] as Array<{ label: string; value: string; dimDesc: string; valDesc: string; examples: string[] }>
    const synthetic = oa.synthetic
    const catalog = synthetic?.catalog
    const dims: any[] = catalog?.dimensions || []
    const mapping = synthetic?.selection?.mapping || {}
    const displayName = (s: string) => s.replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase())
    const results: Array<{ label: string; value: string; dimDesc: string; valDesc: string; examples: string[] }> = []
    for (const [dimKey, valKey] of Object.entries(mapping)) {
      const dim = dims.find(d => d?.name === dimKey)
      const dimLabel = (dim?.label as string) || displayName(dimKey)
      const valueDef = (dim?.values || []).find((v: any) => v?.name === valKey)
      const valueLabel = (valueDef?.label as string) || displayName(String(valKey))
      const dimDesc = dim?.description || ''
      const valDesc = valueDef?.description || ''
      const examples = Array.isArray(valueDef?.examples) ? valueDef.examples : []
      results.push({ label: dimLabel, value: valueLabel, dimDesc, valDesc, examples })
    }
    return results
  }, [traceData?.trace?.metadata])

  // Eval scenarios carry an expected outcome (whatever the scenario YAML
  // declared in its `expect:` block) attached to the trace metadata under
  // metadata.omniagents.expected. Surface every field generically so the
  // panel works for any dataset's reference shape, not just `doctor_answer`.
  const referenceEntries = useMemo<Array<[string, any]>>(() => {
    const md = traceData?.trace?.metadata
    let meta: any = null
    if (md) {
      if (typeof md === 'string') {
        try { meta = JSON.parse(md) } catch { meta = null }
      } else {
        meta = md
      }
    }
    const expected = meta?.omniagents?.expected
    if (!expected || typeof expected !== 'object') return []
    return Object.entries(expected).filter(([, v]) => v !== null && v !== undefined && v !== '')
  }, [traceData?.trace?.metadata])
  const [referenceExpanded, setReferenceExpanded] = useState(true)

  // Reset expanded functions when trace changes and auto-focus conversation area
  React.useEffect(() => {
    setExpandedFunctions(new Set())
    // Auto-focus the conversation area for keyboard scrolling
    setTimeout(() => {
      if (conversationRef.current) {
        conversationRef.current.focus()
      }
    }, 100)
  }, [traceId])
  
  
  const toggleFunctionExpand = (spanId: string) => {
    setExpandedFunctions(prev => {
      const next = new Set(prev)
      if (next.has(spanId)) {
        next.delete(spanId)
      } else {
        next.add(spanId)
      }
      return next
    })
  }
  
  // Sort spans by started_at timestamp
  const sortedSpans = useMemo(() => {
    return [...spans].sort((a, b) => {
      const aTime = a.started_at ? new Date(a.started_at).getTime() : 0
      const bTime = b.started_at ? new Date(b.started_at).getTime() : 0
      return aTime - bTime
    })
  }, [spans])
  
  // Helper function to parse Responses API spans
  const parseResponseSpan = (
    span: Span,
    items: ParsedItem[],
    seenMessages: Set<string>,
    seenToolCalls: Set<string>
  ) => {
    const spanData = span.data?.span_data
    if (!spanData) return
    
    const timestamp = span.started_at || span.created_at
    
    // Parse instructions from the response object if present (system message)
    if (showSystemMessages && spanData.response?.instructions) {
      const instructions = spanData.response.instructions
      // Instructions can be a string or an array of input items
      let instructionContent = ''
      if (typeof instructions === 'string') {
        instructionContent = instructions
      } else if (Array.isArray(instructions)) {
        // Extract text from instruction items
        instructions.forEach((item: any) => {
          if (typeof item === 'string') {
            instructionContent += item
          } else if (item.content) {
            instructionContent += typeof item.content === 'string' ? item.content : JSON.stringify(item.content)
          }
        })
      }
      
      if (instructionContent) {
        const messageKey = `system-response-${instructionContent}`
        if (!seenMessages.has(messageKey)) {
          seenMessages.add(messageKey)
          items.push({
            type: 'message',
            data: {
              role: 'system',
              content: instructionContent,
              timestamp,
              spanId: span.span_id,
              error: span.error
            }
          })
        }
      }
    }
    
    // Parse ALL input messages from conversation history
    if (spanData.input && Array.isArray(spanData.input)) {
      spanData.input.forEach((msg: any) => {
        // Process user messages
        if (msg.role === 'user') {
          const messageKey = `user-${msg.content}`
          if (!seenMessages.has(messageKey)) {
            seenMessages.add(messageKey)
            items.push({
              type: 'message',
              data: {
                role: 'user',
                content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
                timestamp,
                spanId: span.span_id,
                error: span.error
              }
            })
          }
        }
        // Process assistant messages from history
        else if (msg.role === 'assistant' && msg.content) {
          // Extract text content from the Responses API format
          let content = ''
          if (Array.isArray(msg.content)) {
            msg.content.forEach((contentItem: any) => {
              if (contentItem.type === 'output_text' || contentItem.type === 'text') {
                content += contentItem.text || ''
              }
            })
          } else if (typeof msg.content === 'string') {
            content = msg.content
          }
          
          if (content) {
            // Historic assistant messages: the OpenAI Responses API echoes the
            // full conversation history in each response span's ``input``, so
            // the same prior-turn message appears in every span's input. Dedupe
            // by content (NOT by span_id) so we render it once. Including
            // span_id here would produce N copies in a trace with N response
            // spans, since each call to the API has a different span_id.
            const messageKey = msg.id || `assistant-history-${content}`
            if (!seenMessages.has(messageKey)) {
              seenMessages.add(messageKey)
              items.push({
                type: 'message',
                data: {
                  role: 'assistant',
                  content,
                  timestamp,
                  spanId: span.span_id,
                  error: span.error
                }
              })
            }
          }
        }
        // Process function calls from history
        else if (msg.type === 'function_call' && msg.name) {
          const callId = msg.call_id || msg.id
          const funcKey = callId || `func-${msg.name}-${msg.arguments}`
          if (!seenToolCalls.has(funcKey)) {
            seenToolCalls.add(funcKey)
            // Create a function item for display
            items.push({
              type: 'function',
              data: {
                name: msg.name,
                input: msg.arguments ? safeJsonParse(msg.arguments) : null,
                output: null, // Will be populated if we find the output
                timestamp,
                spanId: span.span_id,
                error: span.error,
                isVerbose: expandedFunctions.has(span.span_id)
              }
            })
          }
        }
        // Process function outputs and match them to their calls
        else if (msg.type === 'function_call_output' && msg.call_id) {
          // Find the corresponding function call and update its output
          const funcItem = items.slice().reverse().find((item: ParsedItem) => 
            item.type === 'function' && 
            item.data.spanId === span.span_id &&
            !item.data.output // Only update if output not already set
          )
          if (funcItem && funcItem.type === 'function') {
            funcItem.data.output = msg.output ? safeJsonParse(msg.output) : null
          }
        }
      })
    }
    
    // Parse response output
    if (spanData.response?.output && Array.isArray(spanData.response.output)) {
      spanData.response.output.forEach((output: any) => {
        if (output.type === 'message' && output.role === 'assistant') {
          let content = ''
          if (output.content && Array.isArray(output.content)) {
            output.content.forEach((contentItem: any) => {
              if (contentItem.type === 'output_text' || contentItem.type === 'text') {
                content += contentItem.text || ''
              }
            })
          } else if (typeof output.content === 'string') {
            content = output.content
          }
          
          if (content) {
            // Use message ID for deduplication if available, otherwise use content
            const messageKey = output.id || `assistant-${span.span_id}-${content}`
            if (!seenMessages.has(messageKey)) {
              seenMessages.add(messageKey)
              items.push({
                type: 'message',
                data: {
                  role: 'assistant',
                  content,
                  timestamp,
                  spanId: span.span_id,
                  error: span.error,
                  tokens: spanData.response?.usage
                }
              })
            }
          }
        } else if (output.type === 'function_call' && output.name) {
          // Don't duplicate if we already have this function call from history
          const callId = output.call_id || output.id
          const funcKey = callId || `func-${output.name}-${output.arguments}`
          if (!seenToolCalls.has(funcKey)) {
            seenToolCalls.add(funcKey)
            // This is a new function call from the current response
            items.push({
              type: 'function',
              data: {
                name: output.name,
                input: output.arguments ? 
                  (typeof output.arguments === 'string' ? 
                    safeJsonParse(output.arguments) : 
                    output.arguments) : null,
                output: null,
                timestamp,
                spanId: span.span_id,
                error: span.error,
                isVerbose: expandedFunctions.has(span.span_id)
              }
            })
          }
        }
      })
    }
  }
  
  // Helper function to parse Chat Completions API spans
  const parseGenerationSpan = (
    span: Span,
    items: ParsedItem[],
    seenMessages: Set<string>,
    seenToolCalls: Set<string>
  ) => {
    const spanData = span.data?.span_data
    if (!spanData) return
    
    const timestamp = span.started_at || span.created_at
    
    // Parse input messages - process ALL messages in the conversation history
    if (spanData.input && Array.isArray(spanData.input)) {
      spanData.input.forEach((msg: any) => {
        // Process system messages
        if (msg.role === 'system' && showSystemMessages) {
          const messageKey = `system-${msg.content}`
          if (!seenMessages.has(messageKey)) {
            seenMessages.add(messageKey)
            items.push({
              type: 'message',
              data: {
                role: 'system',
                content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
                timestamp,
                spanId: span.span_id,
                error: span.error
              }
            })
          }
        }
        // Process user messages
        else if (msg.role === 'user') {
          const messageKey = `user-${msg.content}`
          if (!seenMessages.has(messageKey)) {
            seenMessages.add(messageKey)
            items.push({
              type: 'message',
              data: {
                role: 'user',
                content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
                timestamp,
                spanId: span.span_id,
                error: span.error
              }
            })
          }
        }
        // Process assistant messages from conversation history
        else if (msg.role === 'assistant') {
          // Check if this is a tool call or a text response
          if (msg.tool_calls && msg.tool_calls.length > 0) {
            // Create function items for tool calls in history
            msg.tool_calls.forEach((toolCall: any) => {
              const callId = toolCall.id
              const funcKey = callId || `func-${toolCall.function.name}-${toolCall.function.arguments}`
              if (!seenToolCalls.has(funcKey)) {
                seenToolCalls.add(funcKey)
                // Create a function item for display
                items.push({
                  type: 'function',
                  data: {
                    name: toolCall.function.name,
                    input: toolCall.function.arguments ? 
                      (typeof toolCall.function.arguments === 'string' ? 
                        safeJsonParse(toolCall.function.arguments) : 
                        toolCall.function.arguments) : null,
                    output: null, // Will be populated if we find the tool response
                    timestamp,
                    spanId: span.span_id,
                    error: span.error,
                    isVerbose: expandedFunctions.has(span.span_id),
                    callId: callId // Store callId to match with tool responses
                  }
                })
              }
            })
          } else if (msg.content) {
            // Regular assistant message
            const messageKey = `assistant-${msg.content}`
            if (!seenMessages.has(messageKey)) {
              seenMessages.add(messageKey)
              items.push({
                type: 'message',
                data: {
                  role: 'assistant',
                  content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
                  timestamp,
                  spanId: span.span_id,
                  error: span.error
                }
              })
            }
          }
        }
        // Process tool response messages
        else if (msg.role === 'tool') {
          // Match tool responses with their corresponding function calls
          const toolCallId = msg.tool_call_id
          if (toolCallId) {
            // Find the corresponding function call and update its output
            const funcItem = items.slice().reverse().find((item: ParsedItem) => 
              item.type === 'function' && 
              item.data.callId === toolCallId
            )
            if (funcItem && funcItem.type === 'function') {
              funcItem.data.output = msg.content ? safeJsonParse(msg.content) : null
            }
          }
        }
      })
    }
    
    // Parse output (handle nested structure)
    if (spanData.output && Array.isArray(spanData.output)) {
      const output = spanData.output[0] // Usually just one output object
      if (output?.output) {
        // Process output items
        if (Array.isArray(output.output)) {
          output.output.forEach((item: any) => {
            if (item.type === 'function_call' && item.name) {
              // Don't duplicate if we already have this function call from history
              const callId = item.call_id || item.id
              const funcKey = callId || `func-${item.name}-${item.arguments}`
              if (!seenToolCalls.has(funcKey)) {
                seenToolCalls.add(funcKey)
                // This is a new function call from the current response
                items.push({
                  type: 'function',
                  data: {
                    name: item.name,
                    input: item.arguments ? 
                      (typeof item.arguments === 'string' ? 
                        safeJsonParse(item.arguments) : 
                        item.arguments) : null,
                    output: null,
                    timestamp,
                    spanId: span.span_id,
                    error: span.error,
                    isVerbose: expandedFunctions.has(span.span_id)
                  }
                })
              }
            } else if (item.type === 'message' && item.role === 'assistant') {
              // Assistant message response
              let content = ''
              if (Array.isArray(item.content)) {
                item.content.forEach((contentItem: any) => {
                  if (contentItem.type === 'output_text' || contentItem.type === 'text') {
                    content += contentItem.text || ''
                  }
                })
              } else if (typeof item.content === 'string') {
                content = item.content
              }
              
              if (content) {
                items.push({
                  type: 'message',
                  data: {
                    role: 'assistant',
                    content,
                    timestamp,
                    spanId: span.span_id,
                    error: span.error,
                    tokens: output.usage
                  }
                })
              }
            }
          })
        }
      }
    }
  }
  
  // Parse spans into conversation items
  const conversationItems = useMemo(() => {
    const items: ParsedItem[] = []
    const seenMessages = new Set<string>()
    const seenToolCalls = new Set<string>()
    
    // Process all spans based on API type
    sortedSpans.forEach((span) => {
      const timestamp = span.started_at || span.created_at
      
      // Route to appropriate parser based on span type
      if (span.span_type === 'response' && span.data?.span_data) {
        parseResponseSpan(span, items, seenMessages, seenToolCalls)
      } else if (span.span_type === 'generation' && span.data?.span_data) {
        parseGenerationSpan(span, items, seenMessages, seenToolCalls)
      } else if (span.span_type === 'generation' && span.data?.span_data) {
        // Fallback for old parsing logic (will be removed)
        const spanData = span.data.span_data
        
        // Process input messages
        if (spanData.input && Array.isArray(spanData.input)) {
          spanData.input.forEach((msg: any) => {
            // Skip tool messages and assistant messages in input (they're part of history)
            if (msg.role === 'tool' || msg.role === 'assistant') {
              return
            }
            
            if (msg.role === 'user') {
              const messageKey = msg.id || `user-${msg.content}`
              if (!seenMessages.has(messageKey)) {
                seenMessages.add(messageKey)
                items.push({
                  type: 'message',
                  data: {
                    role: 'user',
                    content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
                    timestamp,
                    spanId: span.span_id,
                    error: span.error
                  }
                })
              }
            } else if (msg.role === 'system' && showSystemMessages) {
              const messageKey = msg.id || `system-${msg.content}`
              if (!seenMessages.has(messageKey)) {
                seenMessages.add(messageKey)
                items.push({
                  type: 'message',
                  data: {
                    role: 'system',
                    content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
                    timestamp,
                    spanId: span.span_id,
                    error: span.error
                  }
                })
              }
            }
          })
        }
        
        // Parse output messages (assistant responses) - these are always new
        if (spanData.output && Array.isArray(spanData.output)) {
          spanData.output.forEach((msg: any) => {
            if (msg.role === 'assistant') {
              // Check if this is a tool call or a text response
              if (msg.tool_calls && msg.tool_calls.length > 0) {
                // This is a tool call, we'll show it via function spans
                msg.tool_calls.forEach((toolCall: any) => {
                  if (toolCall.id && !seenToolCalls.has(toolCall.id)) {
                    seenToolCalls.add(toolCall.id)
                  }
                })
              } else if (msg.content) {
                // This is a text response
                let content = ''
                
                // Handle content array structure
                if (Array.isArray(msg.content)) {
                  msg.content.forEach((contentItem: any) => {
                    if (contentItem.type === 'text') {
                      content += contentItem.text
                    }
                  })
                } else if (typeof msg.content === 'string') {
                  content = msg.content
                }
                
                if (content) {
                  items.push({
                    type: 'message',
                    data: {
                      role: 'assistant',
                      content,
                      timestamp,
                      spanId: span.span_id,
                      error: span.error,
                      tokens: spanData.usage
                    }
                  })
                }
              }
            }
          })
        }
      }
      
      // Handle response spans (from OpenAI Responses API)
      else if (span.span_type === 'response' && span.data?.span_data) {
        const spanData = span.data.span_data
        
        // Process input messages from response spans - these show the full conversation history
        if (spanData.input && Array.isArray(spanData.input)) {
          spanData.input.forEach((msg: any) => {
            // For response spans, we want to show the entire conversation history
            // but deduplicate based on content and position
            
            if (msg.role === 'user') {
              const messageKey = msg.id || `user-${msg.content}`
              if (!seenMessages.has(messageKey)) {
                seenMessages.add(messageKey)
                items.push({
                  type: 'message',
                  data: {
                    role: 'user',
                    content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
                    timestamp,
                    spanId: span.span_id,
                    error: span.error
                  }
                })
              }
            }
            // Show assistant messages from history
            else if (msg.role === 'assistant' && msg.content) {
              // Extract text content
              let content = ''
              if (Array.isArray(msg.content)) {
                msg.content.forEach((contentItem: any) => {
                  if (contentItem.type === 'output_text' || contentItem.type === 'text') {
                    content += contentItem.text || ''
                  }
                })
              } else if (typeof msg.content === 'string') {
                content = msg.content
              }
              
              if (content) {
                // Use message ID for deduplication if available
                const messageKey = msg.id || `assistant-${span.span_id}-${content}`
                if (!seenMessages.has(messageKey)) {
                  seenMessages.add(messageKey)
                  items.push({
                    type: 'message',
                    data: {
                      role: 'assistant',
                      content,
                      timestamp,
                      spanId: span.span_id,
                      error: span.error
                    }
                  })
                }
              }
            }
            // Show function calls from history
            else if (msg.type === 'function_call' && msg.name) {
              const funcKey = msg.call_id || msg.id || `func-${msg.name}-${msg.arguments}`
              if (!seenToolCalls.has(funcKey)) {
                seenToolCalls.add(funcKey)
                items.push({
                  type: 'function',
                  data: {
                    name: msg.name,
                    input: msg.arguments ? safeJsonParse(msg.arguments) : null,
                    output: null, // Will be shown in next item if available
                    timestamp,
                    spanId: span.span_id,
                    error: span.error,
                    isVerbose: expandedFunctions.has(span.span_id)
                  }
                })
              }
            }
            // Show function outputs
            else if (msg.type === 'function_call_output' && msg.call_id) {
              // Find the corresponding function call and update its output
              // Use reverse() to find the last matching item
              const reversedItems = [...items].reverse()
              const funcItem = reversedItems.find((item: ParsedItem) => 
                item.type === 'function' && 
                item.data.spanId === span.span_id
              )
              if (funcItem && funcItem.type === 'function') {
                funcItem.data.output = msg.output ? safeJsonParse(msg.output) : null
              }
            }
          })
        }
        
        // Process response output
        if (spanData.response && spanData.response.output && Array.isArray(spanData.response.output)) {
          spanData.response.output.forEach((output: any) => {
            // Handle function calls - track them so function spans don't duplicate
            if (output.type === 'function_call' && output.name) {
              const callId = output.call_id || output.id
              if (callId && !seenToolCalls.has(callId)) {
                seenToolCalls.add(callId)
              }
            }
            // Handle message responses
            else if (output.type === 'message' && output.role === 'assistant') {
              let content = ''
              
              // Handle content array structure
              if (output.content && Array.isArray(output.content)) {
                output.content.forEach((contentItem: any) => {
                  if (contentItem.type === 'output_text' || contentItem.type === 'text') {
                    content += contentItem.text || ''
                  }
                })
              } else if (typeof output.content === 'string') {
                content = output.content
              }
              
              if (content) {
                // Use message ID for deduplication if available, otherwise use content
                const messageKey = output.id || `assistant-${span.span_id}-${content}`
                if (!seenMessages.has(messageKey)) {
                  seenMessages.add(messageKey)
                  items.push({
                    type: 'message',
                    data: {
                      role: 'assistant',
                      content,
                      timestamp,
                      spanId: span.span_id,
                      error: span.error,
                      tokens: spanData.response?.usage
                    }
                  })
                }
              }
            }
          })
        }
      }
      
      else if (span.span_type === 'function' && span.data?.span_data) {
        const spanData = span.data.span_data
        // Check if we've already seen this function call in the conversation history
        // Create a normalized key based on function name and input for matching
        const inputStr = typeof spanData.input === 'string' ? spanData.input : JSON.stringify(spanData.input)
        const funcKey = `func-${spanData.name}-${inputStr}`
        
        // Check if this function was already added from conversation history
        const alreadyExists = items.some(item => 
          item.type === 'function' && 
          item.data.name === spanData.name &&
          JSON.stringify(item.data.input) === JSON.stringify(spanData.input ? safeJsonParse(spanData.input) : null)
        )
        
        if (!alreadyExists) {
          seenToolCalls.add(funcKey)
          items.push({
            type: 'function',
            data: {
              name: spanData.name || 'Unknown Function',
              input: spanData.input ? safeJsonParse(spanData.input) : null,
              output: spanData.output ? safeJsonParse(spanData.output) : null,
              timestamp,
              spanId: span.span_id,
              error: span.error,
              isVerbose: expandedFunctions.has(span.span_id)
            }
          })
        } else {
          // If it already exists, update its output if this span has output
          if (spanData.output) {
            const existingItem = items.find(item => 
              item.type === 'function' && 
              item.data.name === spanData.name &&
              JSON.stringify(item.data.input) === JSON.stringify(spanData.input ? safeJsonParse(spanData.input) : null)
            )
            if (existingItem && existingItem.type === 'function' && !existingItem.data.output) {
              existingItem.data.output = spanData.output ? safeJsonParse(spanData.output) : null
            }
          }
        }
      }
      
      else if (span.span_type === 'agent' && span.data?.span_data) {
        const spanData = span.data.span_data
        items.push({
          type: 'agent',
          data: {
            name: spanData.name || 'Agent',
            timestamp,
            spanId: span.span_id
          }
        })
      }
      
      else if (span.span_type === 'handoff' && span.data?.span_data) {
        const spanData = span.data.span_data
        items.push({
          type: 'handoff',
          data: {
            from: spanData.from_agent || 'Unknown',
            to: spanData.to_agent || 'Unknown',
            timestamp,
            spanId: span.span_id
          }
        })
      }
      
      else if (span.span_type === 'guardrail' && span.data?.span_data) {
        const spanData = span.data.span_data
        if (spanData.triggered) {
          items.push({
            type: 'guardrail',
            data: {
              name: spanData.name || 'Guardrail',
              triggered: spanData.triggered,
              timestamp,
              spanId: span.span_id
            }
          })
        }
      }
      
      else if (span.span_type === 'transcription' && span.data?.span_data) {
        const spanData = span.data.span_data
        if (spanData.output) {
          items.push({
            type: 'voice',
            data: {
              type: 'transcription',
              text: spanData.output,
              timestamp,
              spanId: span.span_id
            }
          })
        }
      }
      
      else if (span.span_type === 'speech' && span.data?.span_data) {
        const spanData = span.data.span_data
        if (spanData.input) {
          items.push({
            type: 'voice',
            data: {
              type: 'speech',
              text: spanData.input,
              timestamp,
              spanId: span.span_id
            }
          })
        }
      }
    })
    
    return items
  }, [sortedSpans, expandedFunctions, showSystemMessages, traceId])
  
  // Handle space key for scrolling and E key for expanding functions
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Only handle space if not in an input/textarea
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return
      }
      
      if (e.key === ' ' && conversationRef.current) {
        e.preventDefault()
        const scrollAmount = conversationRef.current.clientHeight * 0.8
        conversationRef.current.scrollBy({
          top: e.shiftKey ? -scrollAmount : scrollAmount,
          behavior: 'smooth'
        })
      }
      
      // E key to expand/collapse all functions
      if (e.key === 'e' || e.key === 'E') {
        e.preventDefault()
        setExpandedFunctions(prev => {
          // Get all function span IDs from the conversation items
          const allFunctionSpanIds: string[] = []
          conversationItems.forEach(item => {
            if (item.type === 'function' && item.data.spanId) {
              allFunctionSpanIds.push(item.data.spanId)
            }
          })
          
          if (prev.size === allFunctionSpanIds.length && allFunctionSpanIds.length > 0) {
            // All expanded, so collapse all
            return new Set()
          } else {
            // Some or none expanded, so expand all
            return new Set(allFunctionSpanIds)
          }
        })
      }
    }
    
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [conversationItems])
  
  const renderMessage = (item: ParsedMessage) => {
    const isUser = item.role === 'user'
    const isSystem = item.role === 'system'
    
    return (
      <div
        className={cn(
          "flex w-full mb-4",
          isUser ? "justify-end" : "justify-start"
        )}
      >
        <div
          className={cn(
            "max-w-[85%] rounded-lg p-3 transition-all",
            isUser 
              ? "bg-blue-500 text-white dark:bg-blue-600" 
              : isSystem
              ? "bg-muted text-muted-foreground border border-border"
              : "bg-muted",
            item.error && "border-destructive"
          )}
        >
          <div className="flex items-start gap-2">
            <div className="flex-shrink-0 mt-1">
              {isUser ? (
                <User className="w-4 h-4" />
              ) : isSystem ? (
                <Activity className="w-4 h-4" />
              ) : (
                <Bot className="w-4 h-4" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <MarkdownMessage
                content={item.content}
                variant={isUser ? 'user' : isSystem ? 'system' : 'assistant'}
              />
              {item.error && (
                <div className="flex items-center gap-1 mt-2 text-destructive">
                  <AlertCircle className="w-3 h-3" />
                  <span className="text-xs">Error occurred</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    )
  }
  
  const renderFunction = (item: ParsedFunction) => {
    const isExpanded = expandedFunctions.has(item.spanId)
    
    return (
      <div className="w-full mb-4">
        <div
          className={cn(
            "w-full border rounded-lg bg-card transition-all",
            item.error && "border-destructive"
          )}
        >
        <div className="p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Wrench className="w-4 h-4 text-blue-500" />
              <span className="font-medium text-sm">Function: {item.name}</span>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation()
                toggleFunctionExpand(item.spanId)
              }}
              className="p-1 hover:bg-muted rounded"
            >
              {isExpanded ? (
                <ChevronDown className="w-4 h-4" />
              ) : (
                <ChevronRight className="w-4 h-4" />
              )}
            </button>
          </div>
          
          {isExpanded && (
            <div className="mt-3 space-y-2">
              {item.input && (
                <div>
                  <div className="text-xs text-muted-foreground mb-1">Input:</div>
                  <pre className="text-xs bg-muted p-2 rounded overflow-x-auto">
                    {typeof item.input === 'object' ? JSON.stringify(item.input, null, 2) : item.input}
                  </pre>
                </div>
              )}
              {item.output && (
                <div>
                  <div className="text-xs text-muted-foreground mb-1">Output:</div>
                  <pre className="text-xs bg-muted p-2 rounded overflow-x-auto">
                    {typeof item.output === 'object' ? JSON.stringify(item.output, null, 2) : item.output}
                  </pre>
                </div>
              )}
            </div>
          )}
          
          {item.error && (
            <div className="flex items-center gap-1 mt-2 text-destructive">
              <AlertCircle className="w-3 h-3" />
              <span className="text-xs">Function failed</span>
            </div>
          )}
        </div>
        </div>
      </div>
    )
  }
  
  const renderHandoff = (item: ParsedHandoff) => {
    return (
      <div
        className="flex w-full mb-4 justify-center"
      >
        <div className="px-4 py-2 bg-gradient-to-r from-blue-500/10 to-purple-500/10 border border-border rounded-full">
          <div className="flex items-center gap-2 text-sm">
            <Bot className="w-4 h-4" />
            <span>{item.from}</span>
            <ArrowRight className="w-4 h-4" />
            <span>{item.to}</span>
            <Bot className="w-4 h-4" />
          </div>
        </div>
      </div>
    )
  }
  
  const renderGuardrail = (item: ParsedGuardrail) => {
    return (
      <div
        className="flex w-full mb-4 justify-center"
      >
        <div className="px-3 py-2 bg-orange-500/10 border border-orange-500/30 rounded-lg">
          <div className="flex items-center gap-2 text-sm text-orange-600 dark:text-orange-400">
            <Shield className="w-4 h-4" />
            <span>Safety check triggered: {item.name}</span>
          </div>
        </div>
      </div>
    )
  }
  
  const renderVoice = (item: ParsedVoice) => {
    const Icon = item.type === 'transcription' ? Mic : Volume2
    const label = item.type === 'transcription' ? 'User said' : 'Agent said'
    const isUser = item.type === 'transcription'
    
    return (
      <div
        className={cn(
          "flex w-full mb-4",
          isUser ? "justify-end" : "justify-start"
        )}
      >
        <div className="max-w-[85%] px-4 py-2 bg-muted border border-border rounded-lg">
          <div className="flex items-start gap-2">
            <Icon className="w-4 h-4 text-muted-foreground mt-0.5" />
            <div>
              <div className="text-xs text-muted-foreground mb-1">{label}:</div>
              <div className="text-sm">{item.text}</div>
            </div>
          </div>
        </div>
      </div>
    )
  }
  
  const renderAgent = (item: { name: string; timestamp: string; spanId: string }) => {
    return (
      <div
        className="flex w-full mb-4 justify-center"
      >
        <div className="px-3 py-1 text-xs text-muted-foreground bg-muted/50 rounded-full">
          <div className="flex items-center gap-1">
            <Bot className="w-3 h-3" />
            <span>Agent: {item.name}</span>
          </div>
        </div>
      </div>
    )
  }
  
  if (spans.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center">
          <Activity className="w-12 h-12 mx-auto mb-2 opacity-20" />
          <p className="text-sm">Select a trace to review</p>
        </div>
      </div>
    )
  }
  
  return (
    <div className="h-full flex flex-col bg-background">
      {/* Header */}
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Review Mode</h2>
          
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                checked={showSystemMessages}
                onChange={(e) => setShowSystemMessages(e.target.checked)}
                className="rounded border-input accent-blue-500 dark:accent-blue-600"
              />
              Show system messages
            </label>
            <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                checked={showMetadata}
                onChange={(e) => setShowMetadata(e.target.checked)}
                className="rounded border-input accent-blue-500 dark:accent-blue-600"
              />
              Show metadata
            </label>
          </div>
        </div>
        {syntheticBadges.length > 0 && (
          <div className="mt-2">
            <div className="flex flex-wrap gap-2">
              {syntheticBadges.map((b, idx) => (
                <div key={`${b.label}-${idx}`} className="relative group inline-flex">
                  <span className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md bg-muted border border-border">
                    <span className="text-muted-foreground">{b.label}:</span>
                    <span className="font-medium">{b.value}</span>
                  </span>
                  <div className="absolute z-50 top-full left-0 mt-1 w-80 max-w-xs p-2 bg-popover border border-border rounded-md shadow-md text-xs invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-opacity">
                    <div className="font-medium mb-1">{b.label}: {b.value}</div>
                    {b.dimDesc && <div className="text-muted-foreground whitespace-pre-line">{b.dimDesc}</div>}
                    {b.valDesc && (
                      <div className="mt-1 whitespace-pre-line">
                        <span className="text-muted-foreground">Value:</span> {b.value} — {b.valDesc}
                      </div>
                    )}
                    {b.examples && b.examples.length > 0 && (
                      <div className="mt-1 whitespace-pre-line">
                        <span className="text-muted-foreground">Examples:</span> {b.examples.slice(0,3).join('; ')}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      
      {/* Conversation */}
      <div 
        ref={conversationRef}
        className="flex-1 overflow-y-auto p-4 focus:outline-none" 
        tabIndex={0}
      >
        <div key={traceId} className="max-w-4xl mx-auto">
          {showMetadata && (
            <TraceMetadataBlock metadata={traceData?.trace?.metadata} />
          )}
          {referenceEntries.length > 0 && (
            <div className="mb-4 rounded-md border border-blue-500/30 bg-blue-500/5 overflow-hidden">
              <button
                type="button"
                onClick={() => setReferenceExpanded((v) => !v)}
                className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left hover:bg-blue-500/10 transition-colors"
              >
                <span className="font-mono text-[10px] uppercase tracking-wide text-blue-600 dark:text-blue-400">
                  Reference
                </span>
                <span className="text-xs text-muted-foreground shrink-0">
                  {referenceExpanded ? 'Hide' : 'Show'}
                </span>
              </button>
              {referenceExpanded && (
                <dl className="px-3 pb-3 pt-3 space-y-2 border-t border-blue-500/20">
                  {referenceEntries.map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{k.replace(/_/g, ' ')}</dt>
                      <dd className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
                        {typeof v === 'string' ? v : JSON.stringify(v, null, 2)}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          )}
          {conversationItems.map((item, index) => {
            const key = `item-${index}`
            switch (item.type) {
              case 'message':
                return <div key={key}>{renderMessage(item.data)}</div>
              case 'function':
                return <div key={key}>{renderFunction(item.data)}</div>
              case 'handoff':
                return <div key={key}>{renderHandoff(item.data)}</div>
              case 'guardrail':
                return <div key={key}>{renderGuardrail(item.data)}</div>
              case 'voice':
                return <div key={key}>{renderVoice(item.data)}</div>
              case 'agent':
                return <div key={key}>{renderAgent(item.data)}</div>
              default:
                return null
            }
          })}
        </div>
      </div>
    </div>
  )
}

function TraceMetadataBlock({ metadata }: { metadata: any }) {
  let parsed: any = null
  if (metadata) {
    if (typeof metadata === 'string') {
      try {
        parsed = JSON.parse(metadata)
      } catch {
        parsed = metadata
      }
    } else {
      parsed = metadata
    }
  }
  const hasContent = parsed && (typeof parsed === 'object' ? Object.keys(parsed).length > 0 : true)
  if (!hasContent) return null
  return (
    <div className="w-full mb-4">
      <div className="w-full border rounded-lg bg-card">
        <div className="p-3">
          <div className="text-xs text-muted-foreground mb-2">Trace metadata</div>
          <pre className="text-xs bg-muted p-2 rounded overflow-x-auto">
            {typeof parsed === 'object' ? JSON.stringify(parsed, null, 2) : String(parsed)}
          </pre>
        </div>
      </div>
    </div>
  )
}
