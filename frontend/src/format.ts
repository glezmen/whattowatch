import type { MediaType } from './api/client'
import { intlLocale, t, te } from './i18n'

/** ISO (UTC) -> local date and time in the active UI locale. */
export function fmtDateTime(iso?: string | null): string {
  if (!iso) return '–'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return new Intl.DateTimeFormat(intlLocale(), { dateStyle: 'medium', timeStyle: 'short' }).format(d)
}

/** "YYYY-MM-DD" (or ISO) -> localized date; a plain date is not shifted by the timezone. */
export function fmtDate(d?: string | null): string {
  if (!d) return '–'
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d)
  const date = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(d)
  if (Number.isNaN(date.getTime())) return d
  return new Intl.DateTimeFormat(intlLocale(), { dateStyle: 'medium' }).format(date)
}

export function fmtInt(n?: number | null): string {
  return n == null ? '' : n.toLocaleString(intlLocale())
}

export function mediaTypeLabel(mt: MediaType): string {
  return t(mt === 'movie' ? 'mediaType.movie' : 'mediaType.tv')
}

/** Availability / event confidence label (verified, stale, estimated, exact, …). */
export function confidenceLabel(c: string): string {
  return te(`confidence.${c}`) ? t(`confidence.${c}`) : c
}

// Source names are brand names and are not translated.
export const SOURCE_LABELS: Record<string, string> = {
  app: 'App',
  manual: 'App',
  netflix: 'Netflix',
  netflix_mylist: 'Netflix',
  netflix_viewing: 'Netflix',
  netflix_ratings: 'Netflix',
  netflix_export: 'Netflix',
  prime_viewing: 'Prime Video',
  prime_watchlist: 'Prime Video',
  prime_feedback: 'Prime Video',
  prime_like: 'Prime Video',
  prime_video_export: 'Prime Video',
  imdb: 'IMDb',
  imdb_watchlist: 'IMDb',
  imdb_ratings: 'IMDb',
  tmdb: 'TMDB',
  tmdb_watchlist: 'TMDB',
  tmdb_ratings: 'TMDB',
}

export function sourceLabel(s: string): string {
  if (SOURCE_LABELS[s]) return SOURCE_LABELS[s]
  return te(`source.${s}`) ? t(`source.${s}`) : s
}

export function eventTypeLabel(et: string): string {
  return te(`eventType.${et}`) ? t(`eventType.${et}`) : et
}

/** Which page lists events of this type. */
export function eventRoute(et: string): string {
  return et === 'expiring' || et === 'leaving' || et === 'removed' ? '/tavozik' : '/ujdonsagok'
}
