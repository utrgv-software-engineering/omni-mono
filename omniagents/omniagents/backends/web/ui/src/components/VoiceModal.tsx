import React, { useState, useEffect, useRef } from 'react'
import Orb from './Orb'

export function VoiceModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [isMuted, setIsMuted] = useState(false)
  const [audioLevel, setAudioLevel] = useState(0)
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyzerRef = useRef<AnalyserNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    if (!isOpen) return

    let mounted = true

    // Check if mediaDevices is available
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      console.error('getUserMedia not supported in this browser')
      return
    }

    // Request microphone access
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(stream => {
        if (!mounted) {
          stream.getTracks().forEach(track => track.stop())
          return
        }

        streamRef.current = stream
        const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)()
        audioContextRef.current = audioContext

        const analyzer = audioContext.createAnalyser()
        analyzer.fftSize = 256
        analyzerRef.current = analyzer

        const source = audioContext.createMediaStreamSource(stream)
        source.connect(analyzer)

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
        console.error('Microphone access denied:', err)
      })

    return () => {
      mounted = false
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
      }
      if (audioContextRef.current) {
        audioContextRef.current.close()
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
          onClick={() => setIsMuted(!isMuted)}
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
