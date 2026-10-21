<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Settings } from '../api/client'

const s = ref<Settings | null>(null)

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
.nested {
  grid-template-columns: max-content 1fr;
}
</style>
