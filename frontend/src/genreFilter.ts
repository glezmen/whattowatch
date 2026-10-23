import { computed, ref, watch } from 'vue'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

const EXCLUDED_KEY = 'excluded_genres'

function loadExcluded(): string[] {
  try {
    const raw = localStorage.getItem(EXCLUDED_KEY)
    const v: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x !== '') : []
  } catch {
    return []
  }
}

/** Genre the current list page is filtered to (page-session state; cleared on navigation). */
export const activeGenre = ref('')

/** Genres excluded everywhere (a standing preference; persisted in localStorage). */
export const excludedGenres = ref<string[]>(loadExcluded())

watch(
  excludedGenres,
  (v) => {
    try {
      localStorage.setItem(EXCLUDED_KEY, JSON.stringify(v))
    } catch {
      // storage unavailable
    }
  },
  { deep: true },
)

/** Changes whenever the excluded list changes; watch this to reload lists. */
export const excludedKey = computed(() => excludedGenres.value.join('\u0000'))

/** Value for the `exclude_genre` query parameter (undefined when nothing is excluded). */
export function excludeParam(): string[] | undefined {
  return excludedGenres.value.length ? [...excludedGenres.value] : undefined
}

const same = (a: string, b: string): boolean => a.toLowerCase() === b.toLowerCase()

export function isIncluded(genre: string): boolean {
  return activeGenre.value !== '' && same(activeGenre.value, genre)
}

export function isExcluded(genre: string): boolean {
  return excludedGenres.value.some((g) => same(g, genre))
}

export function exclude(genre: string): void {
  if (!isExcluded(genre)) excludedGenres.value = [...excludedGenres.value, genre]
  if (isIncluded(genre)) activeGenre.value = ''
}

export function unexclude(genre: string): void {
  excludedGenres.value = excludedGenres.value.filter((g) => !same(g, genre))
}

export type ChipState = 'neutral' | 'included' | 'excluded'

export function chipState(genre: string): ChipState {
  if (isIncluded(genre)) return 'included'
  if (isExcluded(genre)) return 'excluded'
  return 'neutral'
}

/** i18n key of the chip tooltip: names the action the next click performs. */
export function chipHintKey(genre: string, route: RouteLocationNormalizedLoaded): string {
  if (route.meta.genreList !== true) return 'genreFilter.clickHint'
  const s = chipState(genre)
  return s === 'included' ? 'genreFilter.clickExclude' : s === 'excluded' ? 'genreFilter.clickClear' : 'genreFilter.clickHint'
}

/**
 * Genre chip click. On list pages: neutral -> included (replaces any other included genre) ->
 * excluded -> neutral. On pages without a list: jump to Discover with the genre included.
 */
export function selectGenre(genre: string, router: Router, route: RouteLocationNormalizedLoaded): void {
  if (route.meta.genreList === true) {
    const s = chipState(genre)
    if (s === 'neutral') activeGenre.value = genre
    else if (s === 'included') exclude(genre)
    else unexclude(genre)
  } else {
    unexclude(genre)
    void router.push({ path: '/felfedezes', query: { genre } })
  }
}
