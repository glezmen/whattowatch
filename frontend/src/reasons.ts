// Recommendation reasons rendered from reason_codes; falls back to the backend's Hungarian sentences.
import type { ReasonCode } from './api/client'
import { t, te } from './i18n'
import { preferenceLabel } from './preferenceLabels'

function list(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String)
  return v == null || v === '' ? [] : [String(v)]
}

export function reasonText(rc: ReasonCode): string | null {
  const p = rc.params ?? {}
  switch (rc.code) {
    case 'genre_match':
      return t('reason.genre_match', { genres: list(p.genres).join(', ') })
    case 'shared_genres':
      return t('reason.shared_genres', { genres: list(p.genres).join(', ') })
    case 'preference': {
      const labels = [...list(p.label), ...list(p.labels)]
      return t('reason.preference', { label: labels.map(preferenceLabel).join(', ') })
    }
    case 'external_score': {
      const score = Number(p.vote_average ?? p.score)
      return Number.isFinite(score) && (p.vote_average != null || p.score != null)
        ? t('reason.external_score_value', { score: score.toFixed(1) })
        : t('reason.external_score')
    }
    default:
      return te(`reason.${rc.code}`) ? t(`reason.${rc.code}`) : null
  }
}

/** Translated reasons; an unknown code uses the matching backend sentence when the lists line up. */
export function reasonTexts(codes: ReasonCode[] | null | undefined, fallback: string[] | undefined): string[] {
  if (!codes || !codes.length) return fallback ?? []
  const aligned = fallback && fallback.length === codes.length
  return codes.flatMap((rc, i) => {
    const s = reasonText(rc)
    if (s) return [s]
    return aligned ? [fallback[i]!] : []
  })
}
