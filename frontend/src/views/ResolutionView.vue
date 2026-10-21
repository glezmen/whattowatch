<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import {
  api,
  type CandidateDetails,
  type MediaType,
  type Resolution,
  type ResolutionCandidate,
  type ResolutionContext,
  type SearchHit,
} from '../api/client'
import Pagination from '../components/Pagination.vue'
import PosterWithOverview from '../components/PosterWithOverview.vue'
import { fmtDate, fmtInt, mediaTypeLabel, sourceLabel } from '../format'
import { genreNames } from '../genres'
import { useI18n } from 'vue-i18n'
import { pushToast } from '../toast'

const { t } = useI18n()
const LIMIT = 20
const status = ref<'ambiguous' | 'unresolved'>('ambiguous')
const items = ref<Resolution[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const busy = ref<number | null>(null)
const reimportNeeded = ref(false)

interface SearchState {
  q: string
  media_type: MediaType | ''
  hits: SearchHit[] | null
  loading: boolean
}
const searches = reactive<Record<number, SearchState>>({})

function searchFor(r: Resolution): SearchState {
  if (!searches[r.id]) searches[r.id] = { q: r.title_text ?? r.lookup_key, media_type: '', hits: null, loading: false }
  return searches[r.id]!
}

// --- import context -------------------------------------------------------------------
const VIEWING_SOURCES = new Set(['netflix_viewing', 'prime_viewing'])
const LIST_SOURCES = new Set(['netflix_mylist', 'imdb_watchlist', 'prime_watchlist'])
const RATING_SOURCES = new Set(['netflix_ratings', 'imdb_ratings', 'prime_feedback'])

function sameDay(a: string, b: string): boolean {
  return fmtDate(a) === fmtDate(b)
}

function fmtDuration(seconds: number): string {
  const totalMin = Math.round(seconds / 60)
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  return h ? t('resolution.durationHm', { h, m }) : t('resolution.durationM', { m })
}

/** "Nézve: … · 14 alkalom · 9 ó 40 p" for viewing rows, else the list/rating date. */
function contextLine(r: Resolution): string | null {
  const c = r.context
  if (!c || !c.rows) return null
  if (VIEWING_SOURCES.has(r.source)) {
    const parts: string[] = []
    if (c.first_at && c.last_at) {
      parts.push(
        sameDay(c.first_at, c.last_at)
          ? t('resolution.watchedOn', { date: fmtDate(c.first_at) })
          : t('resolution.watchedRange', { from: fmtDate(c.first_at), to: fmtDate(c.last_at) }),
      )
    }
    parts.push(t('resolution.times', { n: c.rows }, c.rows))
    if (c.total_seconds) parts.push(fmtDuration(c.total_seconds))
    return parts.join(' · ')
  }
  if (!c.first_at) return null
  if (LIST_SOURCES.has(r.source)) return t('resolution.addedToList', { date: fmtDate(c.first_at) })
  if (RATING_SOURCES.has(r.source)) return t('resolution.rated', { date: fmtDate(c.last_at ?? c.first_at) })
  return null
}

function seasonsLine(c: ResolutionContext): string | null {
  if (!c.seasons.length) return null
  const nums = c.seasons.map((s) => s.season).filter((n): n is number => n != null)
  const n = c.seasons.reduce((sum, s) => sum + s.episodes, 0)
  let range = '–'
  if (nums.length) {
    const lo = Math.min(...nums)
    const hi = Math.max(...nums)
    range = lo === hi ? String(lo) : `${lo}–${hi}`
  }
  return t('resolution.seasonsSeen', { range, n })
}

/** Average minutes per viewing for standalone (movie-like) items; null otherwise. */
function watchedMinutes(r: Resolution): number | null {
  const c = r.context
  if (!c || !c.standalone || c.seasons.length || !c.rows || !c.total_seconds) return null
  return c.total_seconds / c.rows / 60
}

// --- candidates ---------------------------------------------------------------------
function posterSource(c: ResolutionCandidate) {
  return { original_title: c.title, poster_path: c.poster_path, overview_hu: c.overview }
}

function candidateMeta(c: ResolutionCandidate): string[] {
  const parts = [c.year != null ? String(c.year) : '–', mediaTypeLabel(c.media_type)]
  const genres = genreNames(c.media_type, c.genre_ids)
  if (genres.length) parts.push(genres.join(', '))
  return parts
}

interface DetailsState {
  loading: boolean
  failed: boolean
  byKey: Record<string, CandidateDetails>
}
const details = reactive<Record<number, DetailsState>>({})
const candKey = (c: { media_type: MediaType; tmdb_id: number }) => `${c.media_type}:${c.tmdb_id}`

async function loadDetails(r: Resolution) {
  const existing = details[r.id]
  if (existing && (existing.loading || Object.keys(existing.byKey).length)) return
  details[r.id] = { loading: true, failed: false, byKey: {} }
  const st = details[r.id]!
  try {
    for (const d of await api.getCandidateDetails(r.id)) st.byKey[candKey(d)] = d
  } catch {
    st.failed = true
  } finally {
    st.loading = false
  }
}

function detailFor(r: Resolution, c: ResolutionCandidate): CandidateDetails | undefined {
  return details[r.id]?.byKey[candKey(c)]
}

function runtimeMatches(r: Resolution, d: CandidateDetails): boolean {
  const watched = watchedMinutes(r)
  if (watched == null || !d.runtime) return false
  return Math.abs(watched - d.runtime) <= d.runtime * 0.1
}

async function load() {
  loading.value = true
  try {
    const page = await api.getResolutions({ status: status.value, limit: LIMIT, offset: offset.value })
    items.value = page.items
    total.value = page.total
  } catch {
    // toast already shown
  } finally {
    loading.value = false
  }
}

function drop(id: number) {
  items.value = items.value.filter((r) => r.id !== id)
  total.value = Math.max(0, total.value - 1)
}

async function choose(r: Resolution, media_type: MediaType, tmdb_id: number) {
  busy.value = r.id
  try {
    const res = await api.setResolution(r.id, media_type, tmdb_id)
    if (res.reimport_needed) reimportNeeded.value = true
    pushToast(t('resolution.saved'))
    drop(r.id)
  } catch {
    // toast already shown
  } finally {
    busy.value = null
  }
}

async function reject(r: Resolution) {
  busy.value = r.id
  try {
    await api.rejectResolution(r.id)
    drop(r.id)
  } catch {
    // toast already shown
  } finally {
    busy.value = null
  }
}

async function runSearch(r: Resolution) {
  const s = searchFor(r)
  if (!s.q.trim()) return
  s.loading = true
  try {
    s.hits = await api.searchTmdb(s.q.trim(), s.media_type)
  } catch {
    s.hits = []
  } finally {
    s.loading = false
  }
}

watch(status, () => {
  offset.value = 0
  load()
})
onMounted(load)

function goto(o: number) {
  offset.value = Math.max(0, o)
  load()
}
</script>

<template>
  <h1>{{ t('resolution.title') }}</h1>
  <div v-if="reimportNeeded" class="banner" role="status">
    <strong>{{ t('resolution.reimportTitle') }}</strong> {{ t('resolution.reimportText') }}
  </div>
  <div class="toolbar">
    <div class="segmented" role="group" :aria-label="t('history.status')">
      <button type="button" :class="{ active: status === 'ambiguous' }" @click="status = 'ambiguous'">{{ t('resolution.ambiguous') }}</button>
      <button type="button" :class="{ active: status === 'unresolved' }" @click="status = 'unresolved'">{{ t('resolution.unresolved') }}</button>
    </div>
  </div>

  <p v-if="loading && !items.length" class="empty"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <p v-else-if="!items.length" class="empty">{{ t('resolution.empty') }}</p>
  <div v-else class="list">
    <section v-for="r in items" :key="r.id" class="card res">
      <header>
        <div class="head-main">
          <strong>{{ r.title_text ?? r.lookup_key }}</strong>
          <p v-if="r.title_text && r.title_text !== r.lookup_key" class="small muted">{{ t('resolution.key', { key: r.lookup_key }) }}</p>
          <p v-if="r.import_rows" class="small muted">{{ t('resolution.importRows', { n: r.import_rows }) }}</p>
        </div>
        <div class="head-ctx">
          <span class="tag">{{ sourceLabel(r.source) }} · {{ r.source }}</span>
          <p v-if="contextLine(r)" class="small">{{ contextLine(r) }}</p>
          <p v-if="r.context?.source_profiles.length" class="small muted">
            {{ t('resolution.profiles', { names: r.context.source_profiles.join(', ') }) }}
          </p>
          <div v-if="r.context?.devices.length" class="chips" :aria-label="t('resolution.devices')">
            <span v-for="d in r.context.devices" :key="d" class="tag">{{ d }}</span>
          </div>
        </div>
      </header>

      <details v-if="r.context && seasonsLine(r.context)" class="seasons small">
        <summary>{{ seasonsLine(r.context) }}<template v-if="r.context.standalone"> · {{ t('resolution.standaloneToo') }}</template></summary>
        <ul>
          <li v-for="s in r.context.seasons" :key="s.season ?? 'none'">
            <strong>{{ s.season != null ? t('resolution.seasonN', { n: s.season }) : t('resolution.seasonNone') }}:</strong>
            {{ s.episode_titles.join(' · ') || '–' }}
            <span v-if="s.episodes > s.episode_titles.length" class="muted">
              {{ t('resolution.episodesMore', { n: s.episodes - s.episode_titles.length }) }}
            </span>
          </li>
        </ul>
      </details>

      <template v-if="r.candidates && r.candidates.length">
        <div class="cand-head">
          <span class="small muted">{{ t('resolution.candidate') }}</span>
          <button
            type="button"
            class="small"
            :title="t('resolution.detailsHint')"
            :disabled="details[r.id]?.loading || !!(details[r.id] && Object.keys(details[r.id]!.byKey).length)"
            @click="loadDetails(r)"
          >
            {{ t('resolution.details') }}
          </button>
          <span v-if="details[r.id]?.loading" class="spinner"></span>
          <span v-else-if="details[r.id]?.failed" class="small muted">{{ t('resolution.detailsError') }}</span>
        </div>
        <ul class="cands">
          <li v-for="c in r.candidates" :key="c.media_type + c.tmdb_id" class="cand">
            <PosterWithOverview :title="posterSource(c)" small hide-lang-note />
            <div class="cand-body">
              <div>
                <strong>{{ c.title }}</strong>
                <span v-if="c.original_title && c.original_title !== c.title" class="muted"> · {{ c.original_title }}</span>
              </div>
              <div class="small muted meta">
                <span>{{ candidateMeta(c).join(' · ') }}</span>
                <span v-if="c.score != null">· {{ t('resolution.score') }} {{ c.score.toFixed(2) }}</span>
                <span v-if="c.vote_count != null">{{ t('resolution.votes', { n: fmtInt(c.vote_count) }) }}</span>
              </div>
              <div class="small badges">
                <span v-if="c.on_provider" class="tag conf-verified">{{ t('resolution.onProvider') }}</span>
                <a
                  v-if="c.tmdb_url"
                  :href="c.tmdb_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  :aria-label="t('resolution.tmdbLinkLabel', { title: c.title })"
                >{{ t('resolution.tmdbLink') }} ↗</a>
              </div>
              <div v-if="detailFor(r, c)" class="small">
                <span v-if="detailFor(r, c)!.error" class="muted">{{ t('resolution.detailsError') }}</span>
                <template v-else-if="c.media_type === 'movie' && detailFor(r, c)!.runtime">
                  <span :class="{ match: runtimeMatches(r, detailFor(r, c)!) }">
                    {{ t('resolution.runtime', { n: detailFor(r, c)!.runtime }) }}
                  </span>
                  <span
                    v-if="runtimeMatches(r, detailFor(r, c)!)"
                    class="tag conf-verified"
                    :title="t('resolution.runtimeMatchTitle', { n: Math.round(watchedMinutes(r) ?? 0) })"
                  >{{ t('resolution.runtimeMatch') }}</span>
                </template>
                <span v-else-if="c.media_type === 'tv' && detailFor(r, c)!.number_of_seasons">
                  {{ t('resolution.seasonsCount', { n: detailFor(r, c)!.number_of_seasons }, detailFor(r, c)!.number_of_seasons!) }}
                </span>
              </div>
            </div>
            <button type="button" class="pick" :disabled="busy === r.id" @click="choose(r, c.media_type, c.tmdb_id)">{{ t('common.thisOne') }}</button>
          </li>
        </ul>
      </template>
      <p v-else class="small muted">{{ t('resolution.noCandidates') }}</p>

      <details>
        <summary class="small">{{ t('resolution.manualSearch') }}</summary>
        <form class="toolbar search" @submit.prevent="runSearch(r)">
          <input v-model="searchFor(r).q" type="search" :aria-label="t('resolution.searchLabel')" />
          <select v-model="searchFor(r).media_type" :aria-label="t('common.type')">
            <option value="">{{ t('common.all') }}</option>
            <option value="movie">{{ t('mediaType.movieCap') }}</option>
            <option value="tv">{{ t('mediaType.tvCap') }}</option>
          </select>
          <button type="submit" :disabled="searchFor(r).loading">{{ t('common.search') }}</button>
          <span v-if="searchFor(r).loading" class="spinner"></span>
        </form>
        <ul v-if="searchFor(r).hits" class="hits small">
          <li v-if="!searchFor(r).hits!.length" class="muted">{{ t('common.noResults') }}</li>
          <li v-for="h in searchFor(r).hits" :key="h.media_type + h.tmdb_id">
            <span>
              {{ h.title }}
              <span v-if="h.original_title !== h.title" class="muted"> · {{ h.original_title }}</span>
              ({{ h.year ?? '?' }}, {{ mediaTypeLabel(h.media_type) }})
            </span>
            <button type="button" :disabled="busy === r.id" @click="choose(r, h.media_type, h.tmdb_id)">{{ t('common.thisOne') }}</button>
          </li>
        </ul>
      </details>

      <div class="actions">
        <button type="button" :disabled="busy === r.id" @click="reject(r)">{{ t('resolution.reject') }}</button>
      </div>
    </section>
  </div>
  <Pagination :total="total" :limit="LIMIT" :offset="offset" @change="goto" />
</template>

<style scoped>
.list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.res {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.res header {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.res header p {
  margin: 2px 0 0;
}
.head-main {
  min-width: 0;
}
.head-ctx {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  text-align: right;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: flex-end;
}
.seasons ul {
  margin: 6px 0 0;
  padding-left: 18px;
}
.cand-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cands {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.cand {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.cand-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-wrap: anywhere;
}
.meta,
.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  align-items: center;
}
.match {
  font-weight: 600;
}
.pick {
  flex-shrink: 0;
}
@media (max-width: 600px) {
  .head-ctx {
    align-items: flex-start;
    text-align: left;
  }
  .chips {
    justify-content: flex-start;
  }
  .cand {
    flex-wrap: wrap;
  }
  .pick {
    margin-left: 70px;
  }
}
.search {
  margin: 8px 0;
}
.hits {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.hits li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.actions {
  display: flex;
  justify-content: flex-end;
}
</style>
