<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, type HistoryItem, type UserState } from '../api/client'
import FeedbackPanel from '../components/FeedbackPanel.vue'
import Pagination from '../components/Pagination.vue'
import { fmtDateTime, mediaTypeLabel, sourceLabel } from '../format'
import { displayTitle, secondaryTitle } from '../titles'

const LIMIT = 50
const q = ref('')
const status = ref('')
const items = ref<HistoryItem[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const expanded = ref<number | null>(null)

const STATUS_LABELS: Record<string, string> = {
  watched: 'megnézve',
  in_progress: 'folyamatban',
  abandoned: 'félbehagyva',
}

async function load() {
  loading.value = true
  try {
    const page = await api.getHistory({ q: q.value.trim(), status: status.value, limit: LIMIT, offset: offset.value })
    items.value = page.items
    total.value = page.total
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
  const st = [...new Set(it.events.map((e) => STATUS_LABELS[e.status] ?? e.status))]
  return st.join(', ')
}
</script>

<template>
  <h1>Előzmények és értékelések</h1>
  <div class="toolbar">
    <input v-model="q" type="search" placeholder="Keresés címre…" aria-label="Keresés" />
    <select v-model="status" aria-label="Státusz">
      <option value="">Minden státusz</option>
      <option v-for="(label, key) in STATUS_LABELS" :key="key" :value="key">{{ label }}</option>
    </select>
  </div>
  <p v-if="loading && !items.length" class="empty"><span class="spinner"></span> Betöltés…</p>
  <p v-else-if="!items.length" class="empty">Nincs előzmény.</p>
  <ul v-else class="history">
    <li v-for="it in items" :key="it.title.id" class="card">
      <button type="button" class="row-head" :aria-expanded="expanded === it.title.id" @click="expanded = expanded === it.title.id ? null : it.title.id">
        <span class="name">
          <strong>{{ displayTitle(it.title) }}</strong>
          <span v-if="secondaryTitle(it.title)" class="muted"> · {{ secondaryTitle(it.title) }}</span>
          <span class="small muted"> ({{ it.title.year ?? '?' }}, {{ mediaTypeLabel(it.title.media_type) }})</span>
        </span>
        <span class="small muted info">
          <span v-if="statusOf(it)">{{ statusOf(it) }}</span>
          <span v-else>nincs nézési esemény</span>
          <span v-if="it.last_watched_at">{{ fmtDateTime(it.last_watched_at) }}</span>
          <span v-if="sourcesOf(it)">{{ sourcesOf(it) }}</span>
          <span v-if="it.title.user.stars">{{ '★'.repeat(it.title.user.stars) }}</span>
          <span v-for="r in it.title.user.personal_ratings" :key="r.source + r.raw_value">
            {{ sourceLabel(r.source) }}: {{ r.raw_value }} [{{ r.scale }}]
          </span>
        </span>
      </button>
      <div v-if="expanded === it.title.id" class="panel">
        <FeedbackPanel :title-id="it.title.id" :user="it.title.user" start-open @update:user="(u) => onUser(it, u)" />
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
  width: 100%;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 4px 12px;
  border: none;
  background: none;
  text-align: left;
  padding: 10px 12px;
}
.info {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
}
.panel {
  padding: 0 12px 12px;
}
</style>
