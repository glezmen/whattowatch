<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, type MediaType, type TitleCard as Card } from '../api/client'
import Pagination from '../components/Pagination.vue'
import TitleCard from '../components/TitleCard.vue'
import { PROVIDERS } from '../providers'

const LIMIT = 24
const f = reactive({ q: '', media_type: '' as MediaType | '', provider: '', genre: '', seen: '' as '' | 'true' | 'false' })
const offset = ref(0)
const items = ref<Card[]>([])
const total = ref(0)
const loading = ref(false)
const knownGenres = ref(new Set<string>())
const genreList = computed(() => [...knownGenres.value].sort((a, b) => a.localeCompare(b, 'hu')))

async function load() {
  loading.value = true
  try {
    const page = await api.listTitles({
      q: f.q.trim(),
      media_type: f.media_type,
      provider: f.provider,
      genre: f.genre.trim(),
      seen: f.seen === '' ? null : f.seen === 'true',
      limit: LIMIT,
      offset: offset.value,
    })
    items.value = page.items
    total.value = page.total
    for (const t of page.items) for (const g of t.genres) knownGenres.value.add(g)
  } catch {
    // toast already shown
  } finally {
    loading.value = false
  }
}

let timer: ReturnType<typeof setTimeout> | undefined
watch(
  () => ({ ...f }),
  () => {
    clearTimeout(timer)
    timer = setTimeout(() => {
      offset.value = 0
      load()
    }, 300)
  },
)
onMounted(load)

function goto(o: number) {
  offset.value = Math.max(0, o)
  load()
  window.scrollTo({ top: 0 })
}
</script>

<template>
  <h1>Felfedezés</h1>
  <p class="small muted">Csak ellenőrzött, előfizetéses magyar elérhetőségű címek a helyi katalógusból.</p>
  <div class="toolbar">
    <input v-model="f.q" type="search" placeholder="Keresés címre…" aria-label="Keresés" />
    <select v-model="f.media_type" aria-label="Típus">
      <option value="">Minden típus</option>
      <option value="movie">Film</option>
      <option value="tv">Sorozat</option>
    </select>
    <input v-model="f.genre" type="text" list="genre-list" placeholder="Műfaj" aria-label="Műfaj" />
    <datalist id="genre-list">
      <option v-for="g in genreList" :key="g" :value="g" />
    </datalist>
    <select v-model="f.seen" aria-label="Látott">
      <option value="">Látott és nem látott</option>
      <option value="false">Csak nem látott</option>
      <option value="true">Csak látott</option>
    </select>
  </div>
  <div class="toolbar" role="group" aria-label="Szolgáltató">
    <button type="button" class="toggle" :class="{ active: f.provider === '' }" @click="f.provider = ''">Mind</button>
    <button
      v-for="p in PROVIDERS"
      :key="p.key"
      type="button"
      class="toggle"
      :class="{ active: f.provider === p.key }"
      @click="f.provider = f.provider === p.key ? '' : p.key"
    >
      {{ p.name }}
    </button>
  </div>

  <p v-if="loading && !items.length" class="empty"><span class="spinner"></span> Betöltés…</p>
  <p v-else-if="!items.length" class="empty">Nincs találat.</p>
  <template v-else>
    <div class="grid">
      <TitleCard v-for="t in items" :key="t.id" :title="t" />
    </div>
    <Pagination :total="total" :limit="LIMIT" :offset="offset" @change="goto" />
  </template>
</template>
