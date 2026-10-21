<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type ReasonCode, type TitleCard, type UserState } from '../api/client'
import { confidenceLabel, fmtDateTime, fmtInt, mediaTypeLabel, sourceLabel } from '../format'
import { reasonTexts } from '../reasons'
import DescriptionToggle from './DescriptionToggle.vue'
import FeedbackPanel from './FeedbackPanel.vue'
import PosterWithOverview from './PosterWithOverview.vue'
import ProviderBadge from './ProviderBadge.vue'
import { displayTitle, secondaryTitle } from '../titles'

const props = defineProps<{
  title: TitleCard
  reasons?: string[]
  reasonCodes?: ReasonCode[] | null
  batchId?: string
  showSources?: boolean
}>()
const emit = defineEmits<{ 'user-change': [user: UserState] }>()

const { t: tr } = useI18n()
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
// displayTitle / reasonTexts read the global i18n locale ref, so these recompute on a language switch
const mainTitle = computed(() => displayTitle(t.value))
const secondary = computed(() => secondaryTitle(t.value))
const reasonList = computed(() => reasonTexts(props.reasonCodes, props.reasons))
const c = computed(() => t.value.community)
</script>

<template>
  <article class="card title-card" :class="{ dimmed: user.not_interested }">
    <div class="top">
      <PosterWithOverview :title="t" />
      <div class="meta">
        <h3>{{ mainTitle }}</h3>
        <p v-if="secondary" class="original">{{ secondary }}</p>
        <p v-if="!t.title_hu && t.has_hu_translation === false" class="hint">{{ tr('card.noHuTitle') }}</p>
        <p class="small muted">
          {{ t.year ?? tr('card.yearUnknown') }} · {{ mediaTypeLabel(t.media_type) }}
          <template v-if="t.media_type === 'tv' && t.number_of_seasons"> · {{ tr('card.seasons', { n: t.number_of_seasons }) }}</template>
          <template v-if="t.media_type === 'movie' && t.runtime"> · {{ tr('card.minutes', { n: t.runtime }) }}</template>
        </p>
        <div v-if="t.genres.length" class="chips">
          <span v-for="g in t.genres" :key="g" class="chip">{{ g }}</span>
        </div>
      </div>
    </div>

    <ul v-if="t.availability.length" class="avail">
      <li
        v-for="a in t.availability"
        :key="a.provider_key + a.offer_type"
        :class="{ off: a.enabled === false }"
        :title="a.enabled === false ? tr('card.notSubscribed') : undefined"
      >
        <ProviderBadge :provider-key="a.provider_key" :fallback-name="a.provider_name" />
        <span class="small muted" :title="tr('card.sourceTitle', { source: a.source })">{{ tr('card.checked', { date: fmtDateTime(a.last_seen_at) }) }}</span>
        <span class="tag" :class="`conf-${a.confidence}`">{{ confidenceLabel(a.confidence) }}</span>
        <span v-if="a.leaving_at" class="small warn">
          {{ tr('card.leaving', { date: fmtDateTime(a.leaving_at) }) }}{{ a.leaving_confidence === 'estimated' ? tr('card.estimatedSuffix') : '' }}
        </span>
      </li>
    </ul>
    <p v-else class="small muted">{{ tr('card.noAvailability') }}</p>

    <div class="scores small" :title="c.fetched_at ? tr('card.fetchedAt', { date: fmtDateTime(c.fetched_at) }) : undefined">
      <span v-if="c.tmdb_vote_average != null">
        TMDB {{ c.tmdb_vote_average.toFixed(1) }}<template v-if="c.tmdb_vote_count"> ({{ fmtInt(c.tmdb_vote_count) }})</template>
      </span>
      <span v-if="c.imdb_rating != null">
        IMDb {{ c.imdb_rating.toFixed(1) }}<template v-if="c.imdb_votes"> ({{ fmtInt(c.imdb_votes) }})</template>
      </span>
      <span v-for="r in user.personal_ratings" :key="r.source + r.raw_value" class="personal">
        {{ tr('card.personal', { source: sourceLabel(r.source), value: r.raw_value, scale: r.scale }) }}
      </span>
    </div>

    <div v-if="showSources && user.watchlist_sources.length" class="chips">
      <span v-for="s in user.watchlist_sources" :key="s" class="tag source">{{ sourceLabel(s) }}</span>
    </div>

    <ul v-if="reasonList.length" class="reasons small">
      <li v-for="r in reasonList" :key="r">{{ r }}</li>
    </ul>

    <DescriptionToggle :overview="t.overview_hu" />

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
.avail li.off {
  opacity: 0.55;
}
</style>
