<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, type UpdateItem, type UpdatesResponse, type UpdateSection } from '../api/client'
import { includeWatchlist } from '../appSettings'
import type { SortOrder } from '../sortOrder'
import UpdateItemCard from './UpdateItemCard.vue'

const props = defineProps<{
  section: UpdateSection
  heading?: string
  days: number
  emptyText?: string
  sort?: SortOrder
  genre?: string
  excludeGenres?: string[]
}>()
const emit = defineEmits<{
  status: [status: UpdatesResponse['source_status']]
  /** Titles left out because they are on the watchlist (X-Filtered-Watchlist). */
  filtered: [count: number]
}>()

const items = ref<UpdateItem[]>([])
const loading = ref(false)
const failed = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    const res = await api.getUpdates(props.section, props.days, 50, includeWatchlist.value, {
      sort: props.sort,
      genre: props.genre || undefined,
      exclude_genre: props.excludeGenres?.length ? props.excludeGenres : undefined,
    })
    items.value = res.items
    emit('status', res.source_status)
    emit('filtered', res.filteredWatchlist)
  } catch {
    failed.value = true
    items.value = []
  } finally {
    loading.value = false
  }
}

watch([() => props.days, () => props.sort, () => props.genre, () => props.excludeGenres?.join('\u0000'), includeWatchlist], load)
onMounted(load)
</script>

<template>
  <section class="update-section">
    <h2 v-if="heading">
      {{ heading }}
      <span v-if="!loading && items.length" class="count small muted">({{ items.length }})</span>
    </h2>
    <p v-if="loading" class="empty"><span class="spinner"></span> {{ $t('common.loading') }}</p>
    <p v-else-if="failed" class="neutral small muted">{{ $t('updates.loadFailed') }}</p>
    <p v-else-if="!items.length" class="neutral small muted">{{ emptyText ?? $t('updates.emptyDefault') }}</p>
    <div v-else class="grid">
      <UpdateItemCard v-for="it in items" :key="it.event.id" :item="it" :section="section" />
    </div>
  </section>
</template>

<style scoped>
.update-section {
  margin-bottom: 28px;
}
h2 {
  font-size: 1.15rem;
  margin: 0 0 10px;
}
.count {
  font-weight: 400;
}
.neutral {
  margin: 0;
  padding: 10px 0;
}
.empty {
  padding: 16px;
}
</style>
