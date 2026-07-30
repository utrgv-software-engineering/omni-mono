import { useEffect, useRef } from 'react'
import { api } from '../../lib/api'
import { useTraceStore } from '../../features/traces/store/traceStore'

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const { addTrace, addSpan } = useTraceStore()

  useEffect(() => {
    let isActive = true

    const cleanupSocket = (socket: WebSocket | null) => {
      if (!socket) return
      socket.onopen = null
      socket.onmessage = null
      socket.onerror = null
      socket.onclose = null
      if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
        socket.close()
      }
    }

    const scheduleReconnect = () => {
      if (!isActive) {
        return
      }
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current)
      }
      reconnectTimerRef.current = window.setTimeout(() => {
        if (isActive) {
          connect()
        }
      }, 5000)
    }

    const handleMessage = (event: MessageEvent) => {
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
            break
          default:
            break
        }
      } catch (error) {
        console.error('Failed to parse WebSocket message:', error)
      }
    }

    const connect = () => {
      if (!isActive) {
        return
      }
      api
        .createWebSocket()
        .then((ws) => {
          if (!isActive) {
            cleanupSocket(ws)
            return
          }
          cleanupSocket(wsRef.current)
          wsRef.current = ws

          ws.onopen = () => {
            console.log('WebSocket connected')
          }

          ws.onmessage = handleMessage

          ws.onerror = (error) => {
            console.error('WebSocket error:', error)
          }

          ws.onclose = () => {
            console.log('WebSocket disconnected')
            if (wsRef.current === ws) {
              wsRef.current = null
            }
            scheduleReconnect()
          }
        })
        .catch((error) => {
          console.error('WebSocket connection failed:', error)
          scheduleReconnect()
        })
    }

    connect()

    return () => {
      isActive = false
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      cleanupSocket(wsRef.current)
      wsRef.current = null
    }
  }, [addTrace, addSpan])

  return wsRef.current
}
