<script setup lang="ts">
import { computed } from 'vue'
import type { UpdateItem, UpdateSection } from '../api/client'
import { useI18n } from 'vue-i18n'
import { confidenceLabel, fmtDate, fmtDateTime, sourceLabel } from '../format'
import ProviderBadge from './ProviderBadge.vue'
import TitleCard from './TitleCard.vue'

const props = defineProps<{ item: UpdateItem; section: UpdateSection }>()

const { t } = useI18n()
const ev = computed(() => props.item.event)
const undated = computed(() => ev.value.payload?.undated === true)

/** Explicit meaning of the date: HU streaming premiere / arrival / new season / leaving. */
const label = computed(() => {
  const e = ev.value
  switch (props.section) {
    case 'new':
      return t('updates.label.new', { date: fmtDate(e.event_date) })
    case 'upcoming':
      return undated.value || !(e.arriving_at ?? e.event_date)
        ? t('updates.label.upcomingUndated')
        : t('updates.label.upcoming', { date: fmtDate(e.arriving_at ?? e.event_date) })
    case 'new_season':
      return t('updates.label.new_season', { date: fmtDate(e.event_date) })
    case 'watchlist_available':
      return t('updates.label.watchlist_available', { date: fmtDate(e.event_date) })
    case 'leaving':
      return undated.value || !(e.leaving_at ?? e.event_date)
        ? t('updates.label.leavingUndated')
        : t('updates.label.leaving', { date: fmtDate(e.leaving_at ?? e.event_date) })
    default:
      return fmtDate(e.event_date)
  }
})

const showConfidence = computed(() => {
  if (undated.value) return false
  return props.section === 'new' || props.section === 'leaving' || props.section === 'upcoming'
})
const warn = computed(() => props.section === 'leaving')
</script>

<template>
  <div class="update-item">
    <div class="strip" :class="{ warn }">
      <ProviderBadge v-if="ev.provider_key" :provider-key="ev.provider_key" :fallback-name="ev.provider_name ?? undefined" />
      <strong class="small">{{ label }}</strong>
      <span v-if="showConfidence" class="tag" :class="`conf-${ev.confidence}`">
        {{ confidenceLabel(ev.confidence) }}
      </span>
      <span v-if="section === 'leaving' && item.title.user.on_watchlist" class="tag wl">{{ t('updates.onWatchlist') }}</span>
      <span class="small muted src" :title="t('updates.detected', { date: fmtDateTime(ev.detected_at) })">{{ t('updates.source', { source: sourceLabel(ev.source) }) }}</span>
    </div>
    <TitleCard :title="item.title" />
  </div>
</template>

<style scoped>
.update-item {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
}
.strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  padding: 6px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-bottom: none;
  border-radius: var(--radius) var(--radius) 0 0;
}
.strip.warn strong {
  color: var(--warn);
}
.update-item :deep(.title-card) {
  border-top-left-radius: 0;
  border-top-right-radius: 0;
  flex: 1;
}
.tag.wl {
  color: var(--accent);
}
.src {
  margin-left: auto;
}
</style>
