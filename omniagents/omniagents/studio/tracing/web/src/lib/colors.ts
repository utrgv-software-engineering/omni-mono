import type { SpanType } from '../types'

export const spanColors: Record<SpanType, string> = {
  agent: '#3B82F6',      // Blue
  function: '#8B5CF6',   // Purple
  generation: '#F97316', // Orange
  handoff: '#14B8A6',    // Teal
  guardrail: '#6A994E',  // Green
  response: '#BC4B51',   // Red-ish
  custom: '#8B8C89',     // Gray
  transcription: '#5C6B73', // Dark gray
  speech: '#826AED',     // Light purple
  'speech-group': '#C879FF', // Lighter purple
  mcp_tools: '#FFB700'   // Yellow
}

export function getSpanColor(type: SpanType): string {
  return spanColors[type] || '#8B8C89'
}