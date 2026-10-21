export interface TitleNames {
  title_hu?: string | null
  title_en?: string | null
  original_title: string
}

export function displayTitle(t: TitleNames): string {
  return t.title_hu || t.title_en || t.original_title
}

/** At most one secondary line: prefer the original title, else the English one when the display is Hungarian. */
export function secondaryTitle(t: TitleNames): string | null {
  const shown = displayTitle(t)
  if (t.original_title && t.original_title !== shown) return t.original_title
  if (t.title_hu && t.title_en && t.title_en !== shown) return t.title_en
  return null
}
