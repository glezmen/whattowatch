<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, itemsOf, type SyncRun } from '../api/client'
import { fmtDateTime } from '../format'

const COLS = ['source', 'started', 'finished', 'status', 'processed', 'created', 'updated', 'skipped', 'errors']
const runs = ref<SyncRun[]>([])
const loading = ref(false)
const open = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    runs.value = itemsOf(await api.getSyncRuns(100))
  } catch {
    // toast already shown
  } finally {
    loading.value = false
  }
}
onMounted(load)

function errText(e: unknown): string {
  return typeof e === 'string' ? e : JSON.stringify(e)
}
</script>

<template>
  <h2>{{ $t('sync.title') }}</h2>
  <div class="toolbar">
    <button type="button" :disabled="loading" @click="load">{{ $t('common.refresh') }}</button>
    <RouterLink to="/import" class="small">{{ $t('sync.hint') }}</RouterLink>
  </div>
  <p v-if="loading && !runs.length" class="empty"><span class="spinner"></span> {{ $t('common.loading') }}</p>
  <p v-else-if="!runs.length" class="empty">{{ $t('sync.empty') }}</p>
  <div v-else class="table-wrap">
    <table class="small">
      <thead>
        <tr>
          <th v-for="c in COLS" :key="c">{{ $t(`sync.cols.${c}`) }}</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="r in runs" :key="r.id">
          <tr>
            <td>{{ r.source }}</td>
            <td>{{ fmtDateTime(r.started_at) }}</td>
            <td>{{ fmtDateTime(r.finished_at) }}</td>
            <td><span class="tag" :class="r.status === 'success' || r.status === 'ok' ? 'conf-verified' : r.status === 'failed' || r.status === 'error' ? 'conf-conflicting' : ''">{{ r.status }}</span></td>
            <td>{{ r.processed }}</td>
            <td>{{ r.created }}</td>
            <td>{{ r.updated }}</td>
            <td>{{ r.skipped }}</td>
            <td>
              <button v-if="r.errors.length" type="button" class="link" @click="open = open === r.id ? null : r.id">
                {{ r.errors.length }} {{ open === r.id ? '▾' : '▸' }}
              </button>
              <span v-else>0</span>
            </td>
          </tr>
          <tr v-if="open === r.id">
            <td colspan="9">
              <ul class="errors">
                <li v-for="(e, i) in r.errors" :key="i"><code>{{ errText(e) }}</code></li>
              </ul>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.errors {
  margin: 0;
  padding-left: 20px;
  max-height: 300px;
  overflow: auto;
}
code {
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
