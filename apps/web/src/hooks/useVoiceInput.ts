import { useEffect, useRef, useState } from 'react'

import { copy, speechLocale } from '../i18n'
import { useStore } from '../state/store'

export function useVoiceInput() {
  const locale = useStore((s) => s.locale)
  const draft = useStore((s) => s.draft)
  const setDraft = useStore((s) => s.setDraft)
  const [listening, setListening] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const recognitionRef = useRef<SpeechRecognition | null>(null)
  const baseRef = useRef('')
  const finalRef = useRef('')
  const heardSpeechRef = useRef(false)
  const errorRef = useRef(false)
  const supported = typeof window !== 'undefined' && Boolean(window.SpeechRecognition || window.webkitSpeechRecognition)

  useEffect(() => () => recognitionRef.current?.abort(), [])

  useEffect(() => {
    recognitionRef.current?.abort()
    recognitionRef.current = null
    setListening(false)
    setMessage(null)
  }, [locale])

  const stop = () => recognitionRef.current?.stop()
  const start = () => {
    if (!supported) { setMessage(copy(locale, 'voiceUnsupported')); return }
    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition
    if (!Recognition) return
    recognitionRef.current?.abort()
    const recognition = new Recognition()
    recognition.lang = speechLocale(locale)
    recognition.continuous = false
    recognition.interimResults = true
    baseRef.current = draft.trim()
    finalRef.current = ''
    heardSpeechRef.current = false
    errorRef.current = false
    recognition.onstart = () => { setListening(true); setMessage(copy(locale, 'listening')) }
    recognition.onresult = (event) => {
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const transcript = event.results[i][0]?.transcript ?? ''
        if (transcript.trim()) heardSpeechRef.current = true
        if (event.results[i].isFinal) finalRef.current += `${transcript} `
        else interim += transcript
      }
      setDraft([baseRef.current, finalRef.current.trim(), interim.trim()].filter(Boolean).join(' '))
    }
    recognition.onerror = (event) => {
      errorRef.current = true
      setListening(false)
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        setMessage(copy(locale, 'voiceDenied'))
      } else if (event.error === 'no-speech') {
        setMessage(copy(locale, 'voiceNoSpeech'))
      } else {
        setMessage(copy(locale, 'voiceError'))
      }
    }
    recognition.onend = () => {
      setListening(false)
      recognitionRef.current = null
      if (!errorRef.current) {
        setMessage(copy(locale, heardSpeechRef.current ? 'voiceCaptured' : 'voiceNoSpeech'))
      }
    }
    recognitionRef.current = recognition
    try { recognition.start() }
    catch { setListening(false); setMessage(copy(locale, 'voiceUnsupported')) }
  }

  return { supported, listening, message, start, stop }
}
