<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { api, type MediaType, type Resolution, type SearchHit } from '../api/client'
import Pagination from '../components/Pagination.vue'
import { mediaTypeLabel, sourceLabel } from '../format'
import { useI18n } from 'vue-i18n'
import { pushToast } from '../toast'

const { t } = useI18n()
const LIMIT = 20
const status = ref<'ambiguous' | 'unresolved'>('ambiguous')
const items = ref<Resolution[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const busy = ref<number | null>(null)
const reimportNeeded = ref(false)

interface SearchState {
  q: string
  media_type: MediaType | ''
  hits: SearchHit[] | null
  loading: boolean
}
const searches = reactive<Record<number, SearchState>>({})

function searchFor(r: Resolution): SearchState {
  if (!searches[r.id]) searches[r.id] = { q: r.title_text ?? r.lookup_key, media_type: '', hits: null, loading: false }
  return searches[r.id]!
}

async function load() {
  loading.value = true
  try {
    const page = await api.getResolutions({ status: status.value, limit: LIMIT, offset: offset.value })
    items.value = page.items
    total.value = page.total
  } catch {
    // toast already shown
  } finally {
    loading.value = false
  }
}

function drop(id: number) {
  items.value = items.value.filter((r) => r.id !== id)
  total.value = Math.max(0, total.value - 1)
}

async function choose(r: Resolution, media_type: MediaType, tmdb_id: number) {
  busy.value = r.id
  try {
    const res = await api.setResolution(r.id, media_type, tmdb_id)
    if (res.reimport_needed) reimportNeeded.value = true
    pushToast(t('resolution.saved'))
    drop(r.id)
  } catch {
    // toast already shown
  } finally {
    busy.value = null
  }
}

async function reject(r: Resolution) {
  busy.value = r.id
  try {
    await api.rejectResolution(r.id)
    drop(r.id)
  } catch {
    // toast already shown
  } finally {
    busy.value = null
  }
}

async function runSearch(r: Resolution) {
  const s = searchFor(r)
  if (!s.q.trim()) return
  s.loading = true
  try {
    s.hits = await api.searchTmdb(s.q.trim(), s.media_type)
  } catch {
    s.hits = []
  } finally {
    s.loading = false
  }
}

watch(status, () => {
  offset.value = 0
  load()
})
onMounted(load)

function goto(o: number) {
  offset.value = Math.max(0, o)
  load()
}
</script>

<template>
  <h1>{{ t('resolution.title') }}</h1>
  <div v-if="reimportNeeded" class="banner" role="status">
    <strong>{{ t('resolution.reimportTitle') }}</strong> {{ t('resolution.reimportText') }}
  </div>
  <div class="toolbar">
    <div class="segmented" role="group" :aria-label="t('history.status')">
      <button type="button" :class="{ active: status === 'ambiguous' }" @click="status = 'ambiguous'">{{ t('resolution.ambiguous') }}</button>
      <button type="button" :class="{ active: status === 'unresolved' }" @click="status = 'unresolved'">{{ t('resolution.unresolved') }}</button>
    </div>
  </div>

  <p v-if="loading && !items.length" class="empty"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <p v-else-if="!items.length" class="empty">{{ t('resolution.empty') }}</p>
  <div v-else class="list">
    <section v-for="r in items" :key="r.id" class="card res">
      <header>
        <div>
          <strong>{{ r.title_text ?? r.lookup_key }}</strong>
          <p v-if="r.title_text && r.title_text !== r.lookup_key" class="small muted">{{ t('resolution.key', { key: r.lookup_key }) }}</p>
          <p v-if="r.import_rows" class="small muted">{{ t('resolution.importRows', { n: r.import_rows }) }}</p>
        </div>
        <span class="tag">{{ sourceLabel(r.source) }} · {{ r.source }}</span>
      </header>

      <table v-if="r.candidates && r.candidates.length" class="small">
        <thead>
          <tr><th>{{ t('resolution.candidate') }}</th><th>{{ t('resolution.year') }}</th><th>{{ t('common.type') }}</th><th>{{ t('resolution.score') }}</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="c in r.candidates" :key="c.media_type + c.tmdb_id">
            <td>
              {{ c.title }}
              <span v-if="c.original_title && c.original_title !== c.title" class="muted"> · {{ c.original_title }}</span>
              <span v-if="c.on_provider" class="tag conf-verified">{{ t('resolution.onProvider') }}</span>
            </td>
            <td>{{ c.year ?? '–' }}</td>
            <td>{{ mediaTypeLabel(c.media_type) }}</td>
            <td>{{ c.score != null ? c.score.toFixed(2) : '–' }}</td>
            <td><button type="button" :disabled="busy === r.id" @click="choose(r, c.media_type, c.tmdb_id)">{{ t('common.thisOne') }}</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="small muted">{{ t('resolution.noCandidates') }}</p>

      <details>
        <summary class="small">{{ t('resolution.manualSearch') }}</summary>
        <form class="toolbar search" @submit.prevent="runSearch(r)">
          <input v-model="searchFor(r).q" type="search" :aria-label="t('resolution.searchLabel')" />
          <select v-model="searchFor(r).media_type" :aria-label="t('common.type')">
            <option value="">{{ t('common.all') }}</option>
            <option value="movie">{{ t('mediaType.movieCap') }}</option>
            <option value="tv">{{ t('mediaType.tvCap') }}</option>
          </select>
          <button type="submit" :disabled="searchFor(r).loading">{{ t('common.search') }}</button>
          <span v-if="searchFor(r).loading" class="spinner"></span>
        </form>
        <ul v-if="searchFor(r).hits" class="hits small">
          <li v-if="!searchFor(r).hits!.length" class="muted">{{ t('common.noResults') }}</li>
          <li v-for="h in searchFor(r).hits" :key="h.media_type + h.tmdb_id">
            <span>
              {{ h.title }}
              <span v-if="h.original_title !== h.title" class="muted"> · {{ h.original_title }}</span>
              ({{ h.year ?? '?' }}, {{ mediaTypeLabel(h.media_type) }})
            </span>
            <button type="button" :disabled="busy === r.id" @click="choose(r, h.media_type, h.tmdb_id)">{{ t('common.thisOne') }}</button>
          </li>
        </ul>
      </details>

      <div class="actions">
        <button type="button" :disabled="busy === r.id" @click="reject(r)">{{ t('resolution.reject') }}</button>
      </div>
    </section>
  </div>
  <Pagination :total="total" :limit="LIMIT" :offset="offset" @change="goto" />
</template>

<style scoped>
.list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.res {
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-x: auto;
}
.res header {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.res header p {
  margin: 2px 0 0;
}
.search {
  margin: 8px 0;
}
.hits {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.hits li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.actions {
  display: flex;
  justify-content: flex-end;
}
</style>
