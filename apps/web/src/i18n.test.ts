import { describe, expect, it } from 'vitest'

import { copy, isLocale, speechLocale, storedLocale } from './i18n'

describe('interface languages', () => {
  it('recognises only supported locale identifiers', () => {
    expect(isLocale('en')).toBe(true)
    expect(isLocale('hi')).toBe(true)
    expect(isLocale('te')).toBe(true)
    expect(isLocale('fr')).toBe(false)
    expect(isLocale(null)).toBe(false)
  })

  it('provides native interface and speech variants', () => {
    expect(copy('hi', 'welcomeTitle')).toContain('कैंपस')
    expect(copy('te', 'welcomeTitle')).toContain('క్యాంపస్')
    expect(speechLocale('hi')).toBe('hi-IN')
    expect(speechLocale('te')).toBe('te-IN')
  })

  it('defaults safely when browser storage is unavailable', () => {
    expect(storedLocale()).toBe('en')
  })
})
