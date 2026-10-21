<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, type UserState, type WatchlistEntry } from '../api/client'
import Pagination from '../components/Pagination.vue'
import TitleCard from '../components/TitleCard.vue'

const LIMIT = 48
const items = ref<WatchlistEntry[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const available = ref(false)
const source = ref('')

const sources = [
  { value: '', label: '' },
  { value: 'app', label: 'App' },
  { value: 'netflix_mylist', label: 'Netflix' },
  { value: 'prime_watchlist', label: 'Prime Video' },
  { value: 'imdb_watchlist', label: 'IMDb' },
  { value: 'tmdb_watchlist', label: 'TMDB' },
]

async function load() {
  loading.value = true
  try {
    const page = await api.getWatchlist({
      source: source.value,
      available: available.value || undefined,
      limit: LIMIT,
      offset: offset.value,
    })
    items.value = page.items
    total.value = page.total
  } catch {
    // toast already shown
  } finally {
    loading.value = false
  }
}

function onUser(e: WatchlistEntry, u: UserState) {
  if (!u.on_watchlist) {
    items.value = items.value.filter((x) => x.title.id !== e.title.id)
    total.value = Math.max(0, total.value - 1)
  }
}

watch([available, source], () => {
  offset.value = 0
  load()
})
onMounted(load)

function goto(o: number) {
  offset.value = Math.max(0, o)
  load()
  window.scrollTo({ top: 0 })
}
</script>

<template>
  <h1>{{ $t('watchlist.title') }}</h1>
  <div class="toolbar">
    <select v-model="source" :aria-label="$t('watchlist.source')">
      <option v-for="s in sources" :key="s.value" :value="s.value">{{ s.value ? s.label : $t('watchlist.allSources') }}</option>
    </select>
    <label><input v-model="available" type="checkbox" /> {{ $t('watchlist.availableOnly') }}</label>
  </div>
  <p class="small muted">
    {{ $t('watchlist.hint') }}
  </p>
  <p v-if="loading && !items.length" class="empty"><span class="spinner"></span> {{ $t('common.loading') }}</p>
  <p v-else-if="!items.length" class="empty">{{ $t('watchlist.empty') }}</p>
  <template v-else>
    <div class="grid">
      <TitleCard v-for="e in items" :key="e.title.id" :title="e.title" show-sources @user-change="(u) => onUser(e, u)" />
    </div>
    <Pagination :total="total" :limit="LIMIT" :offset="offset" @change="goto" />
  </template>
</template>
