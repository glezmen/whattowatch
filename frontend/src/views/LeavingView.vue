<script setup lang="ts">
import { ref } from 'vue'
import type { UpdatesResponse } from '../api/client'
import UpdateSectionList from '../components/UpdateSectionList.vue'
import DaysSelector from '../components/DaysSelector.vue'
import SortSelect from '../components/SortSelect.vue'
import { useSortOrder } from '../sortOrder'
import GenreFilterPill from '../components/GenreFilterPill.vue'
import { activeGenre, excludedGenres } from '../genreFilter'
import WatchlistToggle from '../components/WatchlistToggle.vue'

const days = ref(31)
const sort = useSortOrder('leaving')
const saMissing = ref(false)
const filteredWatchlist = ref(0)

function onStatus(s: UpdatesResponse['source_status']) {
  saMissing.value = s.streaming_availability === false
}
</script>

<template>
  <h1>{{ $t('leaving.title') }}</h1>
  <div v-if="saMissing" class="banner small" role="status">
    {{ $t('saBanner') }}
  </div>
  <div class="toolbar">
    <DaysSelector v-model="days" />
    <SortSelect v-model="sort" :default-label="$t('sort.defaults.leaving')" />
    <WatchlistToggle :hidden="filteredWatchlist" />
    <GenreFilterPill />
    <span class="small muted">{{ $t('leaving.hint') }}</span>
  </div>
  <UpdateSectionList
    section="leaving"
    :days="days"
    :sort="sort"
    :genre="activeGenre || undefined"
    :exclude-genres="excludedGenres"
    :empty-text="$t('leaving.empty')"
    @status="onStatus"
    @filtered="(n: number) => (filteredWatchlist = n)"
  />
</template>
