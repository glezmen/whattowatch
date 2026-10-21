<script setup lang="ts">
import { ref } from 'vue'
import type { UpdatesResponse, UpdateSection } from '../api/client'
import UpdateSectionList from '../components/UpdateSectionList.vue'
import DaysSelector from '../components/DaysSelector.vue'

const days = ref(31)
const saMissing = ref(false)

function onStatus(s: UpdatesResponse['source_status']) {
  if (s.streaming_availability === false) saMissing.value = true
}

const sections: { key: UpdateSection; heading: string; empty: string }[] = [
  { key: 'new', heading: 'Új az előfizetéseiden', empty: 'Ebben az időszakban nem jelent meg új cím az előfizetéseiden.' },
  { key: 'upcoming', heading: 'Hamarosan érkezik', empty: 'Nincs ismert érkező cím.' },
  { key: 'new_season', heading: 'Új évad', empty: 'Ebben az időszakban nincs ismert új évad.' },
  { key: 'watchlist_available', heading: 'Watchlistedről most elérhető', empty: 'Egyik watchlistes cím sem vált most elérhetővé.' },
]
</script>

<template>
  <h1>Újdonságok</h1>
  <div v-if="saMissing" class="banner small" role="status">
    A Streaming Availability forrás nincs beállítva, az érkező és kikerülő adatok hiányoznak.
  </div>
  <div class="toolbar">
    <DaysSelector v-model="days" />
    <span class="small muted">
      A kártyán az év az eredeti megjelenésé; a sávban lévő dátum a magyar streaminges eseményé.
    </span>
  </div>
  <UpdateSectionList
    v-for="s in sections"
    :key="s.key"
    :section="s.key"
    :heading="s.heading"
    :empty-text="s.empty"
    :days="days"
    @status="onStatus"
  />
</template>
