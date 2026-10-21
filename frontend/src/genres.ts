// Static TMDB genre id -> name map (hu/en) for search-hit `genre_ids`. TMDB's own
// /genre/{movie,tv}/list is the source; ids are stable, names may differ slightly.
import { currentLang } from './i18n'
import type { MediaType } from './api/client'

type Names = readonly [hu: string, en: string]

const MOVIE: Record<number, Names> = {
  28: ['Akció', 'Action'],
  12: ['Kaland', 'Adventure'],
  16: ['Animációs', 'Animation'],
  35: ['Vígjáték', 'Comedy'],
  80: ['Bűnügyi', 'Crime'],
  99: ['Dokumentum', 'Documentary'],
  18: ['Dráma', 'Drama'],
  10751: ['Családi', 'Family'],
  14: ['Fantasy', 'Fantasy'],
  36: ['Történelmi', 'History'],
  27: ['Horror', 'Horror'],
  10402: ['Zenei', 'Music'],
  9648: ['Rejtély', 'Mystery'],
  10749: ['Romantikus', 'Romance'],
  878: ['Sci-fi', 'Science Fiction'],
  10770: ['TV film', 'TV Movie'],
  53: ['Thriller', 'Thriller'],
  10752: ['Háborús', 'War'],
  37: ['Western', 'Western'],
}

const TV: Record<number, Names> = {
  10759: ['Akció & kaland', 'Action & Adventure'],
  16: MOVIE[16]!,
  35: MOVIE[35]!,
  80: MOVIE[80]!,
  99: MOVIE[99]!,
  18: MOVIE[18]!,
  10751: MOVIE[10751]!,
  10762: ['Gyerek', 'Kids'],
  9648: MOVIE[9648]!,
  10763: ['Hírek', 'News'],
  10764: ['Valóságshow', 'Reality'],
  10765: ['Sci-fi & fantasy', 'Sci-Fi & Fantasy'],
  10766: ['Szappanopera', 'Soap'],
  10767: ['Talk-show', 'Talk'],
  10768: ['Háborús & politikai', 'War & Politics'],
  37: MOVIE[37]!,
}

/** Names of known genre ids in the UI language; unknown ids are skipped. */
export function genreNames(mediaType: MediaType, ids: readonly number[] | null | undefined): string[] {
  const map = mediaType === 'tv' ? TV : MOVIE
  const idx = currentLang() === 'en' ? 1 : 0
  return (ids ?? []).flatMap((id) => (map[id] ? [map[id]![idx]] : []))
}
