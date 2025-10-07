import { useEffect, useRef } from 'react'
import { api } from '../../lib/api'
import { useTraceStore } from '../../features/traces/store/traceStore'

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const { addTrace, addSpan } = useTraceStore()
  
  useEffect(() => {
    // Create WebSocket connection
    const ws = api.createWebSocket()
    wsRef.current = ws
    
    ws.onopen = () => {
      console.log('WebSocket connected')
    }
    
    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data)
        
        switch (message.type) {
          case 'trace_added':
            addTrace(message.data)
            break
          case 'span_added':
            addSpan(message.data)
            break
          case 'span_updated':
            // Handle span updates if needed
            break
        }
      } catch (error) {
        console.error('Failed to parse WebSocket message:', error)
      }
    }
    
    ws.onerror = (error) => {
      console.error('WebSocket error:', error)
    }
    
    ws.onclose = () => {
      console.log('WebSocket disconnected')
      // Attempt to reconnect after a delay
      setTimeout(() => {
        if (wsRef.current === ws) {
          useWebSocket()
        }
      }, 5000)
    }
    
    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close()
      }
    }
  }, [addTrace, addSpan])
  
  return wsRef.current
}