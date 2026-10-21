<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, type TitleCard, type UserState } from '../api/client'
import { CONFIDENCE_LABELS, fmtDateTime, fmtInt, mediaTypeLabel, sourceLabel } from '../format'
import FeedbackPanel from './FeedbackPanel.vue'
import ProviderBadge from './ProviderBadge.vue'

const IMG_BASE = 'https://image.tmdb.org/t/p/w342'

const props = defineProps<{ title: TitleCard; reasons?: string[]; batchId?: string; showSources?: boolean }>()
const emit = defineEmits<{ 'user-change': [user: UserState] }>()

const user = ref<UserState>(props.title.user)
watch(
  () => props.title.user,
  (u) => (user.value = u),
)

function onUser(u: UserState) {
  const prev = user.value
  user.value = u
  emit('user-change', u)
  if (props.batchId) {
    if (u.seen && !prev.seen) api.recommendationFeedback(props.batchId, props.title.id, 'watched').catch(() => {})
    if (u.not_interested && !prev.not_interested)
      api.recommendationFeedback(props.batchId, props.title.id, 'dismissed').catch(() => {})
  }
}

const t = computed(() => props.title)
const mainTitle = computed(() => t.value.title_hu || t.value.original_title)
const showOriginal = computed(() => !!t.value.title_hu && t.value.original_title !== t.value.title_hu)
const c = computed(() => t.value.community)
</script>

<template>
  <article class="card title-card" :class="{ dimmed: user.not_interested }">
    <div class="top">
      <img v-if="t.poster_path" class="poster" :src="IMG_BASE + t.poster_path" :alt="`${mainTitle} plakát`" loading="lazy" />
      <div v-else class="poster placeholder" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="32" height="32">
          <path d="M4 5h16v14H4zM4 9h16M8 5v4M12 5v4M16 5v4" fill="none" stroke="currentColor" stroke-width="1.5" />
        </svg>
      </div>
      <div class="meta">
        <h3>{{ mainTitle }}</h3>
        <p v-if="showOriginal" class="original">{{ t.original_title }}</p>
        <p v-if="!t.title_hu && t.has_hu_translation === false" class="hint">nincs magyar cím</p>
        <p class="small muted">
          {{ t.year ?? 'év ismeretlen' }} · {{ mediaTypeLabel(t.media_type) }}
          <template v-if="t.media_type === 'tv' && t.number_of_seasons"> · {{ t.number_of_seasons }} évad</template>
          <template v-if="t.media_type === 'movie' && t.runtime"> · {{ t.runtime }} perc</template>
        </p>
        <div v-if="t.genres.length" class="chips">
          <span v-for="g in t.genres" :key="g" class="chip">{{ g }}</span>
        </div>
      </div>
    </div>

    <ul v-if="t.availability.length" class="avail">
      <li v-for="a in t.availability" :key="a.provider_key + a.offer_type">
        <ProviderBadge :provider-key="a.provider_key" :fallback-name="a.provider_name" />
        <span class="small muted" :title="`Forrás: ${a.source}`">ellenőrizve: {{ fmtDateTime(a.last_seen_at) }}</span>
        <span class="tag" :class="`conf-${a.confidence}`">{{ CONFIDENCE_LABELS[a.confidence] ?? a.confidence }}</span>
        <span v-if="a.leaving_at" class="small warn">
          távozik: {{ fmtDateTime(a.leaving_at) }}{{ a.leaving_confidence === 'estimated' ? ' (becsült)' : '' }}
        </span>
      </li>
    </ul>
    <p v-else class="small muted">Nincs ellenőrzött előfizetéses elérhetőség.</p>

    <div class="scores small" :title="c.fetched_at ? `Lekérve: ${fmtDateTime(c.fetched_at)}` : undefined">
      <span v-if="c.tmdb_vote_average != null">
        TMDB {{ c.tmdb_vote_average.toFixed(1) }}<template v-if="c.tmdb_vote_count"> ({{ fmtInt(c.tmdb_vote_count) }})</template>
      </span>
      <span v-if="c.imdb_rating != null">
        IMDb {{ c.imdb_rating.toFixed(1) }}<template v-if="c.imdb_votes"> ({{ fmtInt(c.imdb_votes) }})</template>
      </span>
      <span v-for="r in user.personal_ratings" :key="r.source + r.raw_value" class="personal">
        Saját ({{ sourceLabel(r.source) }}): {{ r.raw_value }} [{{ r.scale }}]
      </span>
    </div>

    <div v-if="showSources && user.watchlist_sources.length" class="chips">
      <span v-for="s in user.watchlist_sources" :key="s" class="tag source">{{ sourceLabel(s) }}</span>
    </div>

    <ul v-if="reasons && reasons.length" class="reasons small">
      <li v-for="r in reasons" :key="r">{{ r }}</li>
    </ul>

    <FeedbackPanel :title-id="t.id" :user="user" @update:user="onUser" />
  </article>
</template>

<style scoped>
.title-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: opacity 0.2s;
}
.title-card.dimmed {
  opacity: 0.5;
  filter: grayscale(0.8);
}
.top {
  display: flex;
  gap: 12px;
}
.poster {
  width: 92px;
  aspect-ratio: 2 / 3;
  flex-shrink: 0;
  object-fit: cover;
  border-radius: 6px;
  background: var(--surface-2);
}
.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
.meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
h3 {
  margin: 0;
  font-size: 1.05rem;
  line-height: 1.25;
}
.meta p {
  margin: 0;
}
.original {
  font-style: italic;
  color: var(--text-muted);
  font-size: 0.9rem;
}
.hint {
  font-size: 0.75rem;
  color: var(--warn);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.avail {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.avail li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.scores {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
}
.personal {
  color: var(--text-muted);
}
.reasons {
  margin: 0;
  padding: 8px 8px 8px 24px;
  background: var(--surface-2);
  border-radius: var(--radius);
}
.warn {
  color: var(--warn);
}
</style>
