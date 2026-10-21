import type { MediaType } from './api/client'

const pad = (n: number) => String(n).padStart(2, '0')

/** ISO (UTC) -> local "YYYY-MM-DD HH:mm". */
export function fmtDateTime(iso?: string | null): string {
  if (!iso) return '–'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function fmtInt(n?: number | null): string {
  return n == null ? '' : n.toLocaleString('hu-HU')
}

export function mediaTypeLabel(t: MediaType): string {
  return t === 'movie' ? 'film' : 'sorozat'
}

export const CONFIDENCE_LABELS: Record<string, string> = {
  verified: 'ellenőrzött',
  stale: 'elavult',
  estimated: 'becsült',
  conflicting: 'ellentmondásos',
  user_denied: 'felhasználó cáfolta',
}

export const SOURCE_LABELS: Record<string, string> = {
  app: 'App',
  manual: 'App',
  netflix: 'Netflix',
  netflix_mylist: 'Netflix',
  netflix_viewing: 'Netflix',
  netflix_ratings: 'Netflix',
  imdb: 'IMDb',
  imdb_watchlist: 'IMDb',
  imdb_ratings: 'IMDb',
  tmdb: 'TMDB',
  tmdb_watchlist: 'TMDB',
  tmdb_ratings: 'TMDB',
  import: 'import',
}

export function sourceLabel(s: string): string {
  return SOURCE_LABELS[s] ?? s
}

/** "YYYY-MM-DD" (or ISO) -> "YYYY-MM-DD" without timezone shifting a plain date. */
export function fmtDate(d?: string | null): string {
  if (!d) return '–'
  if (/^\d{4}-\d{2}-\d{2}$/.test(d)) return d
  return fmtDateTime(d).slice(0, 10)
}

export const EVENT_CONFIDENCE_LABELS: Record<string, string> = {
  exact: 'pontos',
  estimated: 'becsült',
  verified: 'ellenőrzött',
}

export const EVENT_TYPE_LABELS: Record<string, string> = {
  new: 'Új az előfizetéseiden',
  upcoming: 'Hamarosan érkezik',
  expiring: 'Hamarosan távozik',
  leaving: 'Hamarosan távozik',
  removed: 'Kikerült',
  new_season: 'Új évad',
  watchlist_available: 'Watchlistedről elérhető',
}

export function eventTypeLabel(t: string): string {
  return EVENT_TYPE_LABELS[t] ?? t
}

/** Which page lists events of this type. */
export function eventRoute(t: string): string {
  return t === 'expiring' || t === 'leaving' || t === 'removed' ? '/tavozik' : '/ujdonsagok'
}
