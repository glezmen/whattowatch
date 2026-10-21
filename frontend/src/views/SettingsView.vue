<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type RefreshSummary, type Settings } from '../api/client'

const s = ref<Settings | null>(null)
const refreshing = ref(false)
const summary = ref<RefreshSummary | null>(null)

async function refreshNow() {
  refreshing.value = true
  summary.value = null
  try {
    summary.value = await api.postRefresh()
  } catch {
    // toast already shown (409: TMDB key missing)
  } finally {
    refreshing.value = false
  }
}

onMounted(async () => {
  try {
    s.value = await api.getSettings()
  } catch {
    // toast already shown
  }
})

function show(v: unknown): string {
  if (v === null || v === undefined || v === '') return '–'
  if (typeof v === 'boolean') return v ? 'igen' : 'nem'
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}
</script>

<template>
  <h1>Beállítások</h1>
  <p class="small muted">
    Csak olvasható nézet. A beállítások a backend környezeti változóiból és konfigurációjából jönnek (lásd
    <code>.env.example</code>); titkos értékek nem jelennek meg.
  </p>
  <section class="card">
    <h2>Katalógusfrissítés</h2>
    <div class="toolbar refresh">
      <button type="button" class="primary" :disabled="refreshing" @click="refreshNow">
        <span v-if="refreshing" class="spinner" aria-hidden="true"></span>
        Frissítés most
      </button>
      <span class="small muted">
        Lekéri az elérhetőség-változásokat, újdonságokat és értesítéseket. Akár egy percig is eltarthat.
      </span>
    </div>
    <div v-if="summary" class="table-wrap">
      <table class="small">
        <tbody>
          <tr v-for="(v, k) in summary" :key="k">
            <th scope="row">{{ k }}</th>
            <td>{{ v }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  <p v-if="!s" class="empty"><span class="spinner"></span> Betöltés…</p>
  <template v-else>
    <section class="card">
      <h2>Adatforrások és LLM</h2>
      <dl>
        <dt>TMDB API-kulcs beállítva</dt>
        <dd>{{ show(s.tmdb_api_key_set) }}</dd>
        <dt>Netflix-profil</dt>
        <dd>{{ show(s.netflix_profile_name) }}</dd>
        <dt>LM Studio alap-URL</dt>
        <dd>{{ show(s.lm_studio_base_url) }}</dd>
        <template v-if="s.leaving_notice_days !== undefined">
          <dt>Kivezetési értesítés (nappal előtte)</dt>
          <dd>{{ Array.isArray(s.leaving_notice_days) ? s.leaving_notice_days.join(', ') : show(s.leaving_notice_days) }}</dd>
        </template>
      </dl>
    </section>
    <section v-if="s.recommender" class="card">
      <h2>Ajánló konfiguráció (súlyok)</h2>
      <dl>
        <template v-for="(v, k) in s.recommender" :key="k">
          <dt>{{ k }}</dt>
          <dd>
            <dl v-if="v && typeof v === 'object' && !Array.isArray(v)" class="nested">
              <template v-for="(v2, k2) in v as Record<string, unknown>" :key="k2">
                <dt>{{ k2 }}</dt>
                <dd>{{ show(v2) }}</dd>
              </template>
            </dl>
            <template v-else>{{ show(v) }}</template>
          </dd>
        </template>
      </dl>
    </section>
  </template>
</template>

<style scoped>
section {
  margin-bottom: 16px;
}
h2 {
  font-size: 1.1rem;
  margin: 0 0 8px;
}
dl {
  display: grid;
  grid-template-columns: minmax(140px, max-content) 1fr;
  gap: 4px 16px;
  margin: 0;
}
dt {
  color: var(--text-muted);
}
dd {
  margin: 0;
  word-break: break-word;
}
.refresh {
  margin-bottom: 8px;
}
th {
  color: var(--text-muted);
  font-weight: 400;
}
.nested {
  grid-template-columns: max-content 1fr;
}
</style>
