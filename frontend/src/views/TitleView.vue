<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import {
  api,
  ApiError,
  type ImportText,
  type MediaType,
  type SearchHit,
  type SimilarTitleItem,
  type TitleDetails,
  type UserState,
} from '../api/client'
import DescriptionToggle from '../components/DescriptionToggle.vue'
import FeedbackPanel from '../components/FeedbackPanel.vue'
import PosterWithOverview from '../components/PosterWithOverview.vue'
import ProviderLink from '../components/ProviderLink.vue'
import TitleCard from '../components/TitleCard.vue'
import { pushToast } from '../toast'
import { confidenceLabel, fmtDate, fmtDateTime, fmtInt, fmtRaw, mediaTypeLabel, sourceLabel } from '../format'
import { chipHintKey, chipState, selectGenre } from '../genreFilter'
import { updateDocumentTitle } from '../router'
import { displayTitle, secondaryTitle } from '../titles'

const IMG_BASE = 'https://image.tmdb.org/t/p/w500'

const { t: tr } = useI18n()
const route = useRoute()
const router = useRouter()
const title = ref<TitleDetails | null>(null)
const user = ref<UserState | null>(null)
const loading = ref(true)
const notFound = ref(false)
const failed = ref(false)

const mainTitle = computed(() => (title.value ? displayTitle(title.value) : ''))
const secondary = computed(() => (title.value ? secondaryTitle(title.value) : null))
const c = computed(() => title.value?.community)

const similar = ref<SimilarTitleItem[]>([])
const similarLoading = ref(false)

/** Separate, quiet request so the page is not delayed; failures just hide the section. */
async function loadSimilar(id: number) {
  similarLoading.value = true
  try {
    const res = await api.getSimilarTitles(id)
    if (Number(route.params.id) === id) similar.value = res.items
  } catch {
    if (Number(route.params.id) === id) similar.value = []
  } finally {
    if (Number(route.params.id) === id) similarLoading.value = false
  }
}

function onSimilarUser(item: SimilarTitleItem, u: UserState) {
  item.title.user = u
}

async function load() {
  const id = Number(route.params.id)
  loading.value = true
  notFound.value = false
  failed.value = false
  title.value = null
  similar.value = []
  similarLoading.value = false
  try {
    const d = await api.getTitle(id, true)
    if (Number(route.params.id) !== id) return
    title.value = d
    user.value = d.user
    void loadSimilar(id)
  } catch (e) {
    if (Number(route.params.id) !== id) return
    if (e instanceof ApiError && e.status === 404) notFound.value = true
    else failed.value = true
  } finally {
    if (Number(route.params.id) === id) loading.value = false
  }
}

watch(() => route.params.id, load, { immediate: true })
// mainTitle reads the locale ref, so the page title follows a language switch too
watch(
  mainTitle,
  (name) => {
    route.meta.titleText = name || undefined
    updateDocumentTitle()
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  route.meta.titleText = undefined
})

// --- fixing a wrong import mapping ----------------------------------------------------
const fixOpen = ref(false)
const fixQ = ref('')
const fixType = ref<MediaType | ''>('')
const fixHits = ref<SearchHit[] | null>(null)
const fixLoading = ref(false)
const fixBusy = ref(false)

const importTexts = computed<ImportText[]>(() => title.value?.import_texts ?? [])
const hasPersonal = computed(() => {
  const u = user.value
  if (!u) return false
  return (
    u.seen ||
    u.abandoned ||
    u.not_interested ||
    u.on_watchlist ||
    u.stars != null ||
    !!u.liked_text ||
    !!u.disliked_text ||
    u.personal_ratings.length > 0
  )
})
const canFix = computed(() => importTexts.value.length > 0 || hasPersonal.value)

const VIEWING = new Set(['netflix_viewing', 'prime_viewing'])
const LISTS = new Set(['netflix_mylist', 'prime_watchlist', 'imdb_watchlist'])
const RATINGS = new Set(['netflix_ratings', 'prime_feedback', 'imdb_ratings'])
function importLine(i: ImportText): string {
  const kind = VIEWING.has(i.source) ? 'viewing' : LISTS.has(i.source) ? 'list' : RATINGS.has(i.source) ? 'rating' : 'other'
  return tr('titlePage.fix.importText', {
    source: sourceLabel(i.source),
    kind: tr(`titlePage.fix.kind.${kind}`),
    text: i.title_text,
    n: i.rows,
  })
}

function toggleFix() {
  fixOpen.value = !fixOpen.value
  if (fixOpen.value && !fixQ.value) {
    fixQ.value = importTexts.value[0]?.title_text ?? title.value?.original_title ?? ''
    fixType.value = title.value?.media_type ?? ''
    if (fixQ.value) runFixSearch()
  }
}

async function runFixSearch() {
  const q = fixQ.value.trim()
  if (!q) return
  fixLoading.value = true
  try {
    fixHits.value = await api.searchTmdb(q, fixType.value)
  } catch {
    fixHits.value = []
  } finally {
    fixLoading.value = false
  }
}

function isCurrent(h: SearchHit): boolean {
  return !!title.value && h.media_type === title.value.media_type && h.tmdb_id === title.value.tmdb_id
}

function hitPoster(h: SearchHit) {
  return { original_title: h.title, poster_path: h.poster_path, overview_hu: h.overview }
}

async function pickFix(h: SearchHit) {
  if (!title.value || fixBusy.value) return
  const name = h.year ? `${h.title} (${h.year})` : h.title
  if (!window.confirm(tr('titlePage.fix.confirm', { title: name }))) return
  fixBusy.value = true
  try {
    const res = await api.reassignTitle(title.value.id, h.media_type, h.tmdb_id)
    pushToast(tr('titlePage.fix.done', { title: displayTitle(res.title) }))
    fixOpen.value = false
    fixQ.value = ''
    fixHits.value = null
    await router.replace(`/cim/${res.title.id}`)
  } catch {
    // toast already shown
  } finally {
    fixBusy.value = false
  }
}

function back() {
  if (window.history.state?.back) router.back()
  else router.push('/')
}
</script>

<template>
  <div class="title-page">
    <a class="link back" href="/" @click.prevent="back">← {{ tr('titlePage.back') }}</a>

    <p v-if="loading" class="muted"><span class="spinner"></span> {{ tr('common.loading') }}</p>
    <div v-else-if="notFound" class="card">
      <h1>{{ tr('titlePage.notFound') }}</h1>
      <RouterLink to="/">{{ tr('titlePage.home') }}</RouterLink>
    </div>
    <div v-else-if="failed || !title || !user" class="card">
      <p>{{ tr('titlePage.loadFailed') }}</p>
      <RouterLink to="/">{{ tr('titlePage.home') }}</RouterLink>
    </div>

    <article v-else class="detail">
      <div class="poster-col">
        <img v-if="title.poster_path" class="poster" :src="IMG_BASE + title.poster_path" :alt="tr('card.poster', { title: mainTitle })" />
        <div v-else class="poster placeholder" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="48" height="48">
            <path d="M4 5h16v14H4zM4 9h16M8 5v4M12 5v4M16 5v4" fill="none" stroke="currentColor" stroke-width="1.5" />
          </svg>
        </div>
      </div>

      <div class="main">
        <h1>{{ mainTitle }}</h1>
        <p v-if="secondary" class="original">{{ secondary }}</p>
        <p v-if="!title.title_hu && title.has_hu_translation === false" class="hint">{{ tr('card.noHuTitle') }}</p>
        <p v-if="canFix" class="small">
          <button type="button" class="linklike" :aria-expanded="fixOpen" @click="toggleFix">{{ tr('titlePage.fix.link') }}</button>
        </p>
        <section v-if="fixOpen" class="card fix" :aria-label="tr('titlePage.fix.title')">
          <h2>{{ tr('titlePage.fix.title') }}</h2>
          <p class="small muted">{{ tr('titlePage.fix.intro') }}</p>
          <template v-if="importTexts.length">
            <p class="small">{{ tr('titlePage.fix.mappedFrom') }}</p>
            <ul class="imports small">
              <li v-for="i in importTexts" :key="i.source + i.title_text">{{ importLine(i) }}</li>
            </ul>
          </template>
          <p v-else class="small muted">{{ tr('titlePage.fix.noImports') }}</p>
          <form class="toolbar fix-search" @submit.prevent="runFixSearch">
            <input v-model="fixQ" type="search" :aria-label="tr('resolution.searchLabel')" />
            <select v-model="fixType" :aria-label="tr('common.type')">
              <option value="">{{ tr('common.all') }}</option>
              <option value="movie">{{ tr('mediaType.movieCap') }}</option>
              <option value="tv">{{ tr('mediaType.tvCap') }}</option>
            </select>
            <button type="submit" :disabled="fixLoading">{{ tr('common.search') }}</button>
            <span v-if="fixLoading" class="spinner"></span>
          </form>
          <ul v-if="fixHits" class="hits">
            <li v-if="!fixHits.length" class="small muted">{{ tr('common.noResults') }}</li>
            <li v-for="h in fixHits" :key="h.media_type + h.tmdb_id" class="hit">
              <PosterWithOverview :title="hitPoster(h)" small hide-lang-note />
              <div class="hit-body">
                <div>
                  <strong>{{ h.title }}</strong>
                  <span v-if="h.original_title && h.original_title !== h.title" class="muted"> · {{ h.original_title }}</span>
                </div>
                <div class="small muted">
                  {{ h.year ?? '–' }} · {{ mediaTypeLabel(h.media_type) }}
                  · <a :href="`https://www.themoviedb.org/${h.media_type}/${h.tmdb_id}`" target="_blank" rel="noopener noreferrer">{{ tr('titlePage.fix.tmdbLink') }} ↗</a>
                </div>
              </div>
              <span v-if="isCurrent(h)" class="tag">{{ tr('titlePage.fix.current') }}</span>
              <button v-else type="button" class="pick" :disabled="fixBusy" @click="pickFix(h)">{{ tr('common.thisOne') }}</button>
            </li>
          </ul>
        </section>
        <p class="muted">
          {{ title.year ?? tr('card.yearUnknown') }} · {{ mediaTypeLabel(title.media_type) }}
          <template v-if="title.media_type === 'tv' && title.number_of_seasons"> · {{ tr('card.seasons', { n: title.number_of_seasons }) }}</template>
          <template v-if="title.media_type === 'movie' && title.runtime"> · {{ tr('card.minutes', { n: title.runtime }) }}</template>
        </p>
        <div v-if="title.genres.length" class="chips">
          <button v-for="g in title.genres" :key="g" type="button" class="chip chip-button" :class="{ active: chipState(g) === 'included', excluded: chipState(g) === 'excluded' }" :title="$t(chipHintKey(g, route))" @click="selectGenre(g, router, route)">{{ g }}</button>
        </div>

        <div v-if="c" class="scores" :title="c.fetched_at ? tr('card.fetchedAt', { date: fmtDateTime(c.fetched_at) }) : undefined">
          <span v-if="c.tmdb_vote_average != null">
            TMDB {{ c.tmdb_vote_average.toFixed(1) }}<template v-if="c.tmdb_vote_count"> ({{ fmtInt(c.tmdb_vote_count) }})</template>
          </span>
          <span v-if="c.imdb_rating != null">
            IMDb {{ c.imdb_rating.toFixed(1) }}<template v-if="c.imdb_votes"> ({{ fmtInt(c.imdb_votes) }})</template>
          </span>
          <span v-for="r in user.personal_ratings" :key="r.source + r.raw_value" class="personal small">
            {{ tr('card.personal', { source: sourceLabel(r.source), value: fmtRaw(r.source, r.raw_value), scale: r.scale }) }}
          </span>
        </div>

        <section>
          <h2>{{ tr('titlePage.availability') }}</h2>
          <ul v-if="title.availability.length" class="avail">
            <li v-for="a in title.availability" :key="a.provider_key + a.offer_type" :class="{ off: a.enabled === false }" :title="a.enabled === false ? tr('card.notSubscribed') : undefined">
              <ProviderLink
                large
                :title-id="title.id" :provider-key="a.provider_key" :fallback-name="a.provider_name" :deep-link="a.deep_link"
                :checked-at="a.last_seen_at" :confidence="a.confidence" :source="a.source"
              />
              <span v-if="a.leaving_at" class="warn">
                {{ tr('titlePage.leavesOn', { date: fmtDate(a.leaving_at) }) }}
                <span v-if="a.leaving_confidence" class="tag">{{ confidenceLabel(a.leaving_confidence) }}</span>
              </span>
              <span v-if="a.arriving_at">
                {{ tr('titlePage.arrivesOn', { date: fmtDate(a.arriving_at) }) }}
                <span v-if="a.arriving_confidence" class="tag">{{ confidenceLabel(a.arriving_confidence) }}</span>
              </span>
            </li>
          </ul>
          <p v-else class="small muted">{{ tr('card.noAvailability') }}</p>
        </section>

        <section>
          <h2>{{ tr('titlePage.description') }}</h2>
          <DescriptionToggle :overview="title.overview_hu" start-open />
        </section>

        <FeedbackPanel :title-id="title.id" :imdb-id="title.imdb_id" :user="user" start-open @update:user="(u) => (user = u)" />
      </div>
    </article>

    <section v-if="title && !notFound && !failed && (similarLoading || similar.length)" class="similar">
      <h2>{{ tr('titlePage.similar') }}</h2>
      <p class="small muted hint">{{ tr('titlePage.similarHint') }}</p>
      <p v-if="similarLoading" class="muted"><span class="spinner"></span> {{ tr('common.loading') }}</p>
      <div v-else class="grid">
        <TitleCard
          v-for="it in similar"
          :key="it.title.id"
          :title="it.title"
          :reason-codes="it.reason_codes"
          @user-change="(u) => onSimilarUser(it, u)"
        />
      </div>
    </section>
  </div>
</template>

<style scoped>
.similar {
  margin-top: 12px;
}
.similar h2 {
  margin-bottom: 4px;
}
.similar .hint {
  margin: 0 0 12px;
}
.title-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.back {
  align-self: flex-start;
}
.detail {
  display: flex;
  gap: 24px;
  align-items: flex-start;
}
.poster-col {
  flex: 0 0 300px;
  max-width: 40%;
}
.poster {
  width: 100%;
  aspect-ratio: 2 / 3;
  object-fit: cover;
  border-radius: 8px;
  background: var(--surface-2);
}
.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.main h1,
.main p {
  margin: 0;
}
h2 {
  font-size: 1rem;
  margin: 0 0 6px;
}
.original {
  font-style: italic;
  color: var(--text-muted);
}
.hint {
  font-size: 0.8rem;
  color: var(--warn);
}
.chips,
.scores {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
}
.chips {
  gap: 4px;
}
.avail {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.avail li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
}
.avail li.off {
  opacity: 0.55;
}
.warn {
  color: var(--warn);
}
.linklike {
  background: none;
  border: none;
  padding: 0;
  color: var(--text-muted);
  text-decoration: underline;
  cursor: pointer;
  font: inherit;
}
.fix {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.imports {
  margin: 0;
  padding-left: 18px;
}
.hits {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.hit {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.hit-body {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.pick {
  flex-shrink: 0;
}
.personal {
  color: var(--text-muted);
}
@media (max-width: 700px) {
  .detail {
    flex-direction: column;
  }
  .poster-col {
    flex-basis: auto;
    max-width: 260px;
  }
}
</style>
