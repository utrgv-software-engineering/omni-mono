import React, { useState, useEffect, useRef } from 'react'
import Orb from './Orb'
import { RealtimeRPCClient } from '../rpc/realtime'

export function VoiceModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [isMuted, setIsMuted] = useState(true)
  const [audioLevel, setAudioLevel] = useState(0)
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyzerRef = useRef<AnalyserNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const rafRef = useRef<number | null>(null)
  const processorRef = useRef<AudioWorkletNode | null>(null)
  const resampleBufferRef = useRef<Float32Array | null>(null)
  const bufferQueueRef = useRef<{ chunks: Float32Array[]; length: number }>({ chunks: [], length: 0 })
  const clientRef = useRef<RealtimeRPCClient | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const scheduledTimeRef = useRef<number>(0)
  const isMutedRef = useRef<boolean>(true)
  const duplexHoldRef = useRef<boolean>(false)
  const micDisabledByPlaybackRef = useRef<boolean>(false)
  const debugEnabled = /^(1|true|yes)$/i.test(String(new URL(window.location.href).searchParams.get('debug') || ''))
  const workletUrlRef = useRef<string | null>(null)

  useEffect(() => {
    if (!isOpen) return

    let mounted = true
    isMutedRef.current = true

    // Check if mediaDevices is available
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      console.error('getUserMedia not supported in this browser')
      return
    }

    // Request microphone access
    navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      } as any,
    })
      .then(async stream => {
        if (!mounted) {
          stream.getTracks().forEach(track => track.stop())
          return
        }

        streamRef.current = stream
        const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)()
        audioContextRef.current = audioContext
        if (debugEnabled) console.log('[ui] audio ctx', { sampleRate: audioContext.sampleRate })

        const analyzer = audioContext.createAnalyser()
        analyzer.fftSize = 256
        analyzerRef.current = analyzer

        const source = audioContext.createMediaStreamSource(stream)
        source.connect(analyzer)

        // Build and load AudioWorklet module (inline to avoid bundling issues)
        const workletCode = `
          class OmniAudioCapture extends AudioWorkletProcessor {
            process(inputs, outputs) {
              const input = inputs[0];
              if (!input || input.length === 0) return true;
              const channel = input[0];
              if (!channel) return true;
              // Forward to main thread for encoding
              this.port.postMessage(channel);
              // Keep graph active by passing through to an output
              if (outputs && outputs.length && outputs[0] && outputs[0][0]) {
                outputs[0][0].set(channel);
              }
              return true;
            }
          }
          registerProcessor('omni-audio-capture', OmniAudioCapture);
        `
        const blob = new Blob([workletCode], { type: 'application/javascript' })
        const workletUrl = URL.createObjectURL(blob)
        try {
          await audioContext.audioWorklet.addModule(workletUrl)
          workletUrlRef.current = workletUrl
          const node = new AudioWorkletNode(audioContext, 'omni-audio-capture', { numberOfInputs: 1, numberOfOutputs: 1 })
          processorRef.current = node
          source.connect(node)
          const sink = audioContext.createGain()
          sink.gain.value = 0
          node.connect(sink)
          sink.connect(audioContext.destination)
          const onAudioFloats = (inFloats: Float32Array) => {
            if (!mounted) return
            if (isMutedRef.current) return
            if (duplexHoldRef.current) return
            const res = resampleTo24k(inFloats, audioContext.sampleRate)
            const q = bufferQueueRef.current
            q.chunks.push(res)
            q.length += res.length
            const chunkSize = 2400
            while (q.length >= chunkSize) {
              const out = new Float32Array(chunkSize)
              let offset = 0
              while (offset < chunkSize && q.chunks.length) {
                const head = q.chunks[0]
                const take = Math.min(head.length, chunkSize - offset)
                out.set(head.subarray(0, take), offset)
                offset += take
                if (take === head.length) {
                  q.chunks.shift()
                } else {
                  q.chunks[0] = head.subarray(take)
                }
              }
              q.length -= chunkSize
              encodeAndSend(out)
            }
          }

          node.port.onmessage = (ev: MessageEvent) => {
            onAudioFloats(ev.data as Float32Array)
          }
        } catch (e) {
          // Fallback to ScriptProcessorNode for browsers without AudioWorklet
          const proc = audioContext.createScriptProcessor(4096, 1, 1)
          // @ts-ignore
          processorRef.current = proc as any
          source.connect(proc)
          const sink = audioContext.createGain()
          sink.gain.value = 0
          proc.connect(sink)
          sink.connect(audioContext.destination)
          proc.onaudioprocess = (ev: AudioProcessingEvent) => {
            if (!mounted) return
            if (isMutedRef.current) return
            if (duplexHoldRef.current) return
            const inBuf = ev.inputBuffer.getChannelData(0)
            const res = resampleTo24k(inBuf, audioContext.sampleRate)
            const q = bufferQueueRef.current
            q.chunks.push(res)
            q.length += res.length
            const chunkSize = 2400
            while (q.length >= chunkSize) {
              const out = new Float32Array(chunkSize)
              let offset = 0
              while (offset < chunkSize && q.chunks.length) {
                const head = q.chunks[0]
                const take = Math.min(head.length, chunkSize - offset)
                out.set(head.subarray(0, take), offset)
                offset += take
                if (take === head.length) {
                  q.chunks.shift()
                } else {
                  q.chunks[0] = head.subarray(take)
                }
              }
              q.length -= chunkSize
              encodeAndSend(out)
            }
          }
        }

        const floatTo16 = (input: Float32Array) => {
          const out = new Int16Array(input.length)
          for (let i = 0; i < input.length; i++) {
            let s = input[i]
            if (s < -1) s = -1
            if (s > 1) s = 1
            out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
          }
          return new Uint8Array(out.buffer)
        }

        const resampleTo24k = (input: Float32Array, inRate: number) => {
          const ratio = 24000 / inRate
          const outLen = Math.floor(input.length * ratio)
          const out = new Float32Array(outLen)
          for (let i = 0; i < outLen; i++) {
            const pos = i / ratio
            const idx = Math.floor(pos)
            const frac = pos - idx
            const s0 = input[idx] || 0
            const s1 = input[idx + 1] || 0
            out[i] = s0 + (s1 - s0) * frac
          }
          return out
        }

        const encodeAndSend = async (floats: Float32Array, commit: boolean = false) => {
          if (!clientRef.current || !sessionIdRef.current) return
          const bytes = floatTo16(floats)

          // Convert bytes to binary string efficiently using chunking to avoid stack overflow
          const chunkSize = 8192
          let binary = ''
          for (let i = 0; i < bytes.length; i += chunkSize) {
            const chunk = bytes.subarray(i, Math.min(i + chunkSize, bytes.length))
            binary += String.fromCharCode.apply(null, Array.from(chunk))
          }

          const b64 = btoa(binary)
          try {
            if (debugEnabled) console.log('[ui] send audio', { samples: floats.length, bytes: bytes.length, b64: b64.length, commit })
            await clientRef.current.sendAudio(sessionIdRef.current, b64, commit)
          } catch (error) {
            console.error('Failed to send audio:', error)
          }
        }

        const dataArray = new Uint8Array(analyzer.frequencyBinCount)

        const updateAudioLevel = () => {
          if (!mounted || !analyzerRef.current) return

          analyzer.getByteFrequencyData(dataArray)
          const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length
          const normalized = Math.min(average / 128, 1) // Normalize to 0-1 range
          setAudioLevel(normalized)

          rafRef.current = requestAnimationFrame(updateAudioLevel)
        }

        updateAudioLevel()
      })
      .catch(err => {
        console.error('Audio setup error:', err)
      })

    return () => {
      mounted = false
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
      }
      if (processorRef.current) {
        try { processorRef.current.disconnect() } catch {}
      }
      if (audioContextRef.current) {
        audioContextRef.current.close()
      }
      if (workletUrlRef.current) {
        try { URL.revokeObjectURL(workletUrlRef.current) } catch {}
        workletUrlRef.current = null
      }
    }
  }, [isOpen])

  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.getAudioTracks().forEach(track => {
        track.enabled = !isMuted
      })
    }
  }, [isMuted])

  useEffect(() => {
    if (!isOpen) return
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.host
    const base = `${proto}://${host}/ws/realtime`
    const url = new URL(window.location.href)
    const token = url.searchParams.get('token') || undefined
    const client = new RealtimeRPCClient(base, token, debugEnabled)
    if (debugEnabled) console.log('[ui] VoiceModal init', { base, token, debug: debugEnabled })
    clientRef.current = client
    let active = true
    const onEvent = client.on('realtime_event', (p: any) => {
      const t = String(p?.type || '')
      if (debugEnabled) console.log('[ui] event', t, p)
      if (t === 'realtime_response_start' || t === 'realtime_audio') {
        duplexHoldRef.current = true
        try {
          const tracks = streamRef.current?.getAudioTracks() || []
          tracks.forEach(tr => { if (tr.enabled) { tr.enabled = false; micDisabledByPlaybackRef.current = true } })
        } catch {}
      }
      if (t === 'realtime_audio') {
        const b64 = String(p?.audio_base64 || '')
        if (!b64) return
        try {
          const bin = atob(b64)
          const bytes = new Uint8Array(bin.length)
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
          const view = new DataView(bytes.buffer)
          const samples = new Float32Array(bytes.length / 2)
          for (let i = 0; i < samples.length; i++) {
            const s = view.getInt16(i * 2, true)
            samples[i] = s / 0x8000
          }
          const ctx = audioContextRef.current
          if (!ctx) return
          try { ctx.resume().catch(() => {}) } catch {}
          const buffer = ctx.createBuffer(1, samples.length, 24000)
          buffer.getChannelData(0).set(samples)
          const src = ctx.createBufferSource()
          src.buffer = buffer
          const startAt = Math.max(ctx.currentTime, scheduledTimeRef.current)
          src.connect(ctx.destination)
          try { src.start(startAt) } catch {}
          scheduledTimeRef.current = startAt + buffer.duration
          if (debugEnabled) console.log('[ui] play audio', { bytes: bytes.length, samples: samples.length, startAt })
        } catch {}
      }
      if (t === 'realtime_audio_end') {
        setTimeout(() => {
          duplexHoldRef.current = false
          try {
            const tracks = streamRef.current?.getAudioTracks() || []
            if (micDisabledByPlaybackRef.current) {
              tracks.forEach(tr => { tr.enabled = true })
              micDisabledByPlaybackRef.current = false
            }
          } catch {}
        }, 150)
      }
    })
    client.connect()
      .then(async () => {
        if (!active) return
        try {
          const res = await client.startSession()
          sessionIdRef.current = String(res?.session_id || '')
          if (debugEnabled) console.log('[ui] session started', res)
        } catch {}
      })
      .catch((e) => { if (debugEnabled) console.error('[ui] connect failed', e) })
    return () => {
      active = false
      onEvent()
      const sid = sessionIdRef.current
      if (debugEnabled) console.log('[ui] cleanup', { sid })
      if (sid && clientRef.current) clientRef.current.stopSession(sid).catch(() => {})
      client.disconnect()
      clientRef.current = null
      sessionIdRef.current = null
      scheduledTimeRef.current = 0
    }
  }, [isOpen])

  if (!isOpen) return null

  const hoverIntensity = isMuted ? 0 : Math.min(audioLevel * 3, 0.1)
  const noiseAmplitude = isMuted ? 0.5 : Math.min(0.5 + audioLevel * 1.0, 0.7)  // 0.5 when silent, caps at 0.7
  const scale = isMuted ? 0.5 : Math.min(0.5 + audioLevel * 1.0, 1.5)  // 0.5 when silent, up to 1.5 when speaking

  return (
    <div className="fixed inset-0 z-50 bg-black/95 flex flex-col items-center justify-center">
      {/* Center orb */}
      <div className="flex-1 flex items-center justify-center w-full max-w-2xl px-8">
        <div
          className="w-full h-full max-h-[500px] transition-transform duration-100 ease-out"
          style={{ transform: `scale(${scale})` }}
        >
          <Orb
            hue={0}
            hoverIntensity={hoverIntensity}
            rotateOnHover={true}
            forceHoverState={!isMuted}
            noiseAmplitude={noiseAmplitude}
            noiseScale={0.35}
            innerRadius={0.2}
          />
        </div>
      </div>

      {/* Bottom controls */}
      <div className="pb-12 flex items-center gap-6">
        {/* Mute/Unmute button */}
        <button
          type="button"
          onClick={async () => {
            const next = !isMuted
            setIsMuted(next)
            isMutedRef.current = next
            if (!next) {
              try { await audioContextRef.current?.resume() } catch {}
            }
            if (next) {
              // No manual commit; rely on server VAD. Just mute capture.
            }
          }}
          className="h-16 w-16 rounded-full bg-tweetBlue hover:brightness-110 flex items-center justify-center shadow-lg"
          aria-label={isMuted ? 'Unmute' : 'Mute'}
        >
          {isMuted ? (
            <svg width="24" height="24" viewBox="0 0 24 24" className="text-white" aria-hidden="true">
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor"/>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none"/>
              <line x1="2" y1="2" x2="22" y2="22" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
            </svg>
          ) : (
            <svg width="24" height="24" viewBox="0 0 24 24" className="text-white" aria-hidden="true">
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor"/>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none"/>
            </svg>
          )}
        </button>

        {/* Close button */}
        <button
          type="button"
          onClick={onClose}
          className="h-16 w-16 rounded-full bg-bgCardAlt hover:bg-bgCard flex items-center justify-center shadow-lg"
          aria-label="Close voice mode"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" className="text-white" aria-hidden="true">
            <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
          </svg>
        </button>
      </div>
    </div>
  )
}
