<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import type { UpdatesResponse, UpdateSection } from '../api/client'
import UpdateSectionList from '../components/UpdateSectionList.vue'
import DaysSelector from '../components/DaysSelector.vue'
import WatchlistToggle from '../components/WatchlistToggle.vue'

const days = ref(31)
const saMissing = ref(false)
const filtered = reactive<Partial<Record<UpdateSection, number>>>({})
const filteredTotal = computed(() => Object.values(filtered).reduce((a, b) => a + (b ?? 0), 0))

function onStatus(s: UpdatesResponse['source_status']) {
  if (s.streaming_availability === false) saMissing.value = true
}

const sections: UpdateSection[] = ['new', 'upcoming', 'new_season', 'watchlist_available']
</script>

<template>
  <h1>{{ $t('updates.title') }}</h1>
  <div v-if="saMissing" class="banner small" role="status">
    {{ $t('saBanner') }}
  </div>
  <div class="toolbar">
    <DaysSelector v-model="days" />
    <WatchlistToggle :hidden="filteredTotal" />
    <span class="small muted">
      {{ $t('updates.hint') }}
    </span>
  </div>
  <UpdateSectionList
    v-for="s in sections"
    :key="s"
    :section="s"
    :heading="$t(`updates.sections.${s}`)"
    :empty-text="$t(`updates.empty.${s}`)"
    :days="days"
    @status="onStatus"
    @filtered="(n: number) => (filtered[s] = n)"
  />
</template>
