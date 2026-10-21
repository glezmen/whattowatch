// UI language: vue-i18n (composition mode). Hungarian is the source of truth.
import { createI18n } from 'vue-i18n'
import en from './locales/en.json'
import hu from './locales/hu.json'

export type UiLang = 'hu' | 'en'
export const UI_LANGS: UiLang[] = ['hu', 'en']
const STORAGE_KEY = 'ui_language'

export function isUiLang(v: unknown): v is UiLang {
  return v === 'hu' || v === 'en'
}

function storedLang(): UiLang | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return isUiLang(v) ? v : null
  } catch {
    return null
  }
}

function browserLang(): UiLang {
  const langs = typeof navigator !== 'undefined' ? navigator.languages ?? [navigator.language] : []
  for (const l of langs) {
    const base = l?.slice(0, 2).toLowerCase()
    if (isUiLang(base)) return base
  }
  return 'hu'
}

export const i18n = createI18n({
  legacy: false,
  locale: storedLang() ?? browserLang(),
  fallbackLocale: 'hu',
  messages: { hu, en },
})

export const t = i18n.global.t
export const te = i18n.global.te

export function currentLang(): UiLang {
  const l = i18n.global.locale.value
  return isUiLang(l) ? l : 'hu'
}

/** Switches the UI locale and remembers it in localStorage (the server copy is written by appSettings). */
export function applyLang(lang: UiLang): void {
  i18n.global.locale.value = lang
  document.documentElement.lang = lang
  try {
    localStorage.setItem(STORAGE_KEY, lang)
  } catch {
    // storage may be unavailable (private mode); the in-memory locale still applies
  }
}

/** BCP 47 tag for Intl formatting. */
export function intlLocale(): string {
  return currentLang() === 'en' ? 'en-GB' : 'hu-HU'
}

document.documentElement.lang = currentLang()
