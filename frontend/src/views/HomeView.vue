<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, type MediaType, type RecommendationItem } from '../api/client'
import TitleCard from '../components/TitleCard.vue'
import { fmtDateTime } from '../format'

const items = ref<RecommendationItem[]>([])
const loading = ref(false)
const refreshing = ref(false)
const loaded = ref(false)
const mediaType = ref<MediaType | ''>('')

async function load(refresh = false) {
  if (refresh) refreshing.value = true
  else loading.value = true
  try {
    const data = await api.getRecommendations({ limit: 20, media_type: mediaType.value, refresh: refresh || undefined })
    items.value = data
    loaded.value = true
  } catch {
    // toast already shown
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

watch(mediaType, () => load())
onMounted(() => load())

const types: { value: MediaType | ''; label: string }[] = [
  { value: '', label: 'Mind' },
  { value: 'movie', label: 'Film' },
  { value: 'tv', label: 'Sorozat' },
]
</script>

<template>
  <h1>Neked ajánljuk</h1>
  <div class="toolbar">
    <div class="segmented" role="group" aria-label="Típus">
      <button v-for="t in types" :key="t.value" type="button" :class="{ active: mediaType === t.value }" @click="mediaType = t.value">
        {{ t.label }}
      </button>
    </div>
    <button type="button" class="primary" :disabled="refreshing" @click="load(true)">
      <span v-if="refreshing" class="spinner" aria-hidden="true"></span>
      Frissítés
    </button>
    <span v-if="refreshing" class="small muted">Új ajánlások készülnek; a TMDB-elérhetőség ellenőrzése eltarthat egy ideig…</span>
    <span v-else-if="items.length" class="small muted">Generálva: {{ fmtDateTime(items[0]!.generated_at) }}</span>
  </div>

  <p v-if="loading" class="empty"><span class="spinner"></span> Betöltés…</p>
  <p v-else-if="loaded && !items.length" class="empty">Még nincs ajánlás. Kattints a „Frissítés” gombra.</p>
  <div v-else class="grid">
    <TitleCard v-for="it in items" :key="it.batch_id + it.title.id" :title="it.title" :reasons="it.reasons" :batch-id="it.batch_id" />
  </div>
</template>
