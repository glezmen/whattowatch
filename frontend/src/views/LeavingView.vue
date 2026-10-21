<script setup lang="ts">
import { ref } from 'vue'
import type { UpdatesResponse } from '../api/client'
import UpdateSectionList from '../components/UpdateSectionList.vue'
import DaysSelector from '../components/DaysSelector.vue'

const days = ref(31)
const saMissing = ref(false)

function onStatus(s: UpdatesResponse['source_status']) {
  saMissing.value = s.streaming_availability === false
}
</script>

<template>
  <h1>Hamarosan távozik</h1>
  <div v-if="saMissing" class="banner small" role="status">
    A Streaming Availability forrás nincs beállítva, az érkező és kikerülő adatok hiányoznak.
  </div>
  <div class="toolbar">
    <DaysSelector v-model="days" />
    <span class="small muted">Kivezetési dátum csak forrásból származik; ha nem ismert, „hamarosan” címkét kap.</span>
  </div>
  <UpdateSectionList
    section="leaving"
    :days="days"
    sort-by-date
    empty-text="Ebben az időszakban nincs ismert kikerülő cím az előfizetéseiden."
    @status="onStatus"
  />
</template>
