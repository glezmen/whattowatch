import { currentLang } from './i18n'

export interface TitleNames {
  title_hu?: string | null
  title_en?: string | null
  original_title: string
}

/** Hungarian UI: HU, then EN, then original. English UI: EN, then original, then HU. */
export function displayTitle(t: TitleNames): string {
  if (currentLang() === 'en') return t.title_en || t.original_title || t.title_hu || ''
  return t.title_hu || t.title_en || t.original_title
}

/** At most one secondary line: prefer the original title, else the other-language title. */
export function secondaryTitle(t: TitleNames): string | null {
  const shown = displayTitle(t)
  if (t.original_title && t.original_title !== shown) return t.original_title
  if (currentLang() === 'en') {
    if (t.title_hu && t.title_hu !== shown) return t.title_hu
  } else if (t.title_hu && t.title_en && t.title_en !== shown) {
    return t.title_en
  }
  return null
}
