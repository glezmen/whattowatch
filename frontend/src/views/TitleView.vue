<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError, type TitleDetails, type UserState } from '../api/client'
import DescriptionToggle from '../components/DescriptionToggle.vue'
import FeedbackPanel from '../components/FeedbackPanel.vue'
import ProviderLink from '../components/ProviderLink.vue'
import { confidenceLabel, fmtDate, fmtDateTime, fmtInt, fmtRaw, mediaTypeLabel, sourceLabel } from '../format'
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

async function load() {
  const id = Number(route.params.id)
  loading.value = true
  notFound.value = false
  failed.value = false
  title.value = null
  try {
    const d = await api.getTitle(id, true)
    title.value = d
    user.value = d.user
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound.value = true
    else failed.value = true
  } finally {
    loading.value = false
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
        <p class="muted">
          {{ title.year ?? tr('card.yearUnknown') }} · {{ mediaTypeLabel(title.media_type) }}
          <template v-if="title.media_type === 'tv' && title.number_of_seasons"> · {{ tr('card.seasons', { n: title.number_of_seasons }) }}</template>
          <template v-if="title.media_type === 'movie' && title.runtime"> · {{ tr('card.minutes', { n: title.runtime }) }}</template>
        </p>
        <div v-if="title.genres.length" class="chips">
          <span v-for="g in title.genres" :key="g" class="chip">{{ g }}</span>
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
  </div>
</template>

<style scoped>
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
