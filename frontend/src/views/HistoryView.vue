<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type HistoryItem, type UserState } from '../api/client'
import { includeWatchlist } from '../appSettings'
import DescriptionToggle from '../components/DescriptionToggle.vue'
import FeedbackPanel from '../components/FeedbackPanel.vue'
import GenreFilterPill from '../components/GenreFilterPill.vue'
import { activeGenre } from '../genreFilter'
import Pagination from '../components/Pagination.vue'
import PosterWithOverview from '../components/PosterWithOverview.vue'
import SortSelect from '../components/SortSelect.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import WatchlistToggle from '../components/WatchlistToggle.vue'
import { fmtDateTime, fmtRaw, fmtStars, mediaTypeLabel, sourceLabel } from '../format'
import { useSortOrder } from '../sortOrder'
import { displayTitle, secondaryTitle } from '../titles'

const LIMIT = 50
const q = ref('')
const status = ref('')
const items = ref<HistoryItem[]>([])
const total = ref(0)
const offset = ref(0)
const sort = useSortOrder('history')
const loading = ref(false)
const filteredWatchlist = ref(0)
const expanded = ref<number | null>(null)
function toggleRow(id: number): void {
  expanded.value = expanded.value === id ? null : id
}
const HIDE_KEY = 'history_hide_rated'
function readHide(): boolean {
  try {
    return localStorage.getItem(HIDE_KEY) === '1'
  } catch {
    return false
  }
}
const hideRated = ref(readHide())

const STATUSES = ['watched', 'in_progress', 'abandoned']
const { t, te } = useI18n()

function statusLabel(s: string): string {
  return te(`history.statuses.${s}`) ? t(`history.statuses.${s}`) : s
}

async function load() {
  loading.value = true
  try {
    const page = await api.getHistory({
      q: q.value.trim(),
      status: status.value,
      exclude_rated: hideRated.value,
      include_watchlist: includeWatchlist.value,
      genre: activeGenre.value || undefined,
      sort: sort.value,
      limit: LIMIT,
      offset: offset.value,
    })
    items.value = page.items
    total.value = page.total
    filteredWatchlist.value = page.filteredWatchlist
  } catch {
    // toast already shown
  } finally {
    loading.value = false
  }
}

function onUser(item: HistoryItem, u: UserState) {
  item.title = { ...item.title, user: u }
}

let timer: ReturnType<typeof setTimeout> | undefined
watch(hideRated, (v) => {
  try {
    localStorage.setItem(HIDE_KEY, v ? '1' : '0')
  } catch {
    // storage unavailable
  }
  clearTimeout(timer)
  offset.value = 0
  load()
})
watch([includeWatchlist, sort, activeGenre], () => {
  clearTimeout(timer)
  offset.value = 0
  load()
})
watch([q, status], () => {
  clearTimeout(timer)
  timer = setTimeout(() => {
    offset.value = 0
    load()
  }, 300)
})
onMounted(load)

function goto(o: number) {
  offset.value = Math.max(0, o)
  load()
}

function sourcesOf(it: HistoryItem): string {
  return [...new Set(it.events.map((e) => sourceLabel(e.source)))].join(', ')
}

function statusOf(it: HistoryItem): string {
  const st = [...new Set(it.events.map((e) => statusLabel(e.status)))]
  return st.join(', ')
}
</script>

<template>
  <h1>{{ t('history.title') }}</h1>
  <div class="toolbar">
    <div class="toggle-stack">
      <ToggleSwitch v-model="hideRated" class="small" :label="t('history.hideRated')" />
      <WatchlistToggle :hidden="filteredWatchlist" />
    </div>
    <input v-model="q" type="search" :placeholder="t('common.searchPlaceholder')" :aria-label="t('common.search')" />
    <select v-model="status" :aria-label="t('history.status')">
      <option value="">{{ t('history.allStatuses') }}</option>
      <option v-for="s in STATUSES" :key="s" :value="s">{{ statusLabel(s) }}</option>
    </select>
    <SortSelect v-model="sort" :default-label="t('sort.defaults.history')" />
    <GenreFilterPill />
  </div>
  <p v-if="loading && !items.length" class="empty"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <p v-else-if="!items.length" class="empty">{{ t('history.empty') }}</p>
  <ul v-else class="history">
    <li v-for="it in items" :key="it.title.id" class="card">
      <div class="row-head" role="button" tabindex="0" :aria-expanded="expanded === it.title.id" @click="toggleRow(it.title.id)" @keydown.enter.prevent="toggleRow(it.title.id)" @keydown.space.prevent="toggleRow(it.title.id)">
        <button type="button" class="chev" tabindex="-1" :aria-label="t('titlePage.toggleRow')" @click.stop="toggleRow(it.title.id)">
          {{ expanded === it.title.id ? '▾' : '▸' }}
        </button>
        <span class="name">
          <RouterLink :to="`/cim/${it.title.id}`" class="title-link" @click.stop><strong>{{ displayTitle(it.title) }}</strong></RouterLink>
          <span v-if="secondaryTitle(it.title)" class="muted"> · {{ secondaryTitle(it.title) }}</span>
          <span class="small muted"> ({{ it.title.year ?? '?' }}, {{ mediaTypeLabel(it.title.media_type) }})</span>
        </span>
        <span class="small muted info">
          <span v-if="statusOf(it)">{{ statusOf(it) }}</span>
          <span v-else>{{ t('history.noEvents') }}</span>
          <span v-if="it.last_watched_at">{{ fmtDateTime(it.last_watched_at) }}</span>
          <span v-if="sourcesOf(it)">{{ sourcesOf(it) }}</span>
          <span v-if="it.title.user.stars">{{ '★ ' + fmtStars(it.title.user.stars) }}</span>
          <span v-for="r in it.title.user.personal_ratings" :key="r.source + r.raw_value">
            {{ sourceLabel(r.source) }}: {{ fmtRaw(r.source, r.raw_value) }} [{{ r.scale }}]
          </span>
        </span>
      </div>
      <div v-if="expanded === it.title.id" class="panel">
        <PosterWithOverview :title="it.title" class="panel-poster" />
        <div class="panel-main">
          <FeedbackPanel :title-id="it.title.id" :imdb-id="it.title.imdb_id" :user="it.title.user" start-open @update:user="(u) => onUser(it, u)" />
          <DescriptionToggle :overview="it.title.overview_hu" />
        </div>
      </div>
    </li>
  </ul>
  <Pagination :total="total" :limit="LIMIT" :offset="offset" @change="goto" />
</template>

<style scoped>
.history {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.history .card {
  padding: 0;
}
.row-head {
  cursor: pointer;
  user-select: none;
  width: 100%;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 4px 12px;
  border: none;
  background: none;
  text-align: left;
  padding: 10px 12px;
  align-items: baseline;
}
.name {
  flex: 1;
  min-width: 0;
}
.chev {
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0 4px;
}
.title-link {
  color: inherit;
  text-decoration: none;
}
.title-link:hover {
  text-decoration: underline;
}
.info {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
}
.panel {
  padding: 0 12px 12px;
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.panel-poster {
  --poster-width: 140px;
}
.panel-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
@media (max-width: 600px) {
  .panel {
    flex-direction: column;
  }
}
</style>
