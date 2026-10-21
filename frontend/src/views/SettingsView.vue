<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type RefreshSummary, type Settings } from '../api/client'
import { DATA_RESTORED_EVENT, loadAppSettings, loadSubscriptions } from '../appSettings'
import BackupSection from '../components/BackupSection.vue'
import LanguageSwitch from '../components/LanguageSwitch.vue'
import LlmSection from '../components/LlmSection.vue'
import SecretsSection from '../components/SecretsSection.vue'
import SubscriptionToggles from '../components/SubscriptionToggles.vue'

const { t } = useI18n()
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

// Bumped after a key / base URL change so the LLM section re-reads key_set and the LM Studio model list.
const llmReload = ref(0)

async function onSecretsSaved() {
  llmReload.value++
  try {
    s.value = await api.getSettings()
  } catch {
    // toast already shown
  }
}

/** After a DB restore: re-read everything that came from the old database. */
async function onRestored() {
  summary.value = null
  window.dispatchEvent(new Event(DATA_RESTORED_EVENT))
  await Promise.all([onSecretsSaved(), loadAppSettings(), loadSubscriptions(true)])
}

function show(v: unknown): string {
  if (v === null || v === undefined || v === '') return '–'
  if (typeof v === 'boolean') return v ? t('common.yes') : t('common.no')
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}
</script>

<template>
  <h1>{{ t('settings.title') }}</h1>

  <section class="card">
    <h2>{{ t('settings.streaming') }}</h2>
    <p class="small muted">{{ t('subscriptions.hint') }}</p>
    <SubscriptionToggles />
    <p class="small muted after">
      {{ t('settings.dataSourcesHint') }}
      <RouterLink to="/import">{{ t('settings.openImport') }} →</RouterLink>
    </p>
  </section>

  <section id="api-keys" class="card">
    <h2>{{ t('settings.secrets.title') }}</h2>
    <SecretsSection @saved="onSecretsSaved" />
  </section>

  <section class="card">
    <h2>{{ t('settings.llm.title') }}</h2>
    <LlmSection :reload-token="llmReload" />
  </section>

  <section class="card">
    <h2>{{ t('settings.backup.title') }}</h2>
    <BackupSection @restored="onRestored" />
  </section>

  <section class="card">
    <h2>{{ t('settings.language') }}</h2>
    <p class="small muted">{{ t('settings.languageHint') }}</p>
    <LanguageSwitch />
  </section>

  <section class="card">
    <h2>{{ t('settings.refresh.title') }}</h2>
    <div class="toolbar refresh">
      <button type="button" class="primary" :disabled="refreshing" @click="refreshNow">
        <span v-if="refreshing" class="spinner" aria-hidden="true"></span>
        {{ t('settings.refresh.button') }}
      </button>
      <span class="small muted">{{ t('settings.refresh.hint') }}</span>
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

  <p v-if="!s" class="empty"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <template v-else>
    <section class="card">
      <h2>{{ t('settings.env.title') }}</h2>
      <p class="small muted">{{ t('settings.env.hint') }}</p>
      <dl>
        <dt>{{ t('settings.env.tmdbKey') }}</dt>
        <dd>{{ show(s.tmdb_api_key_set) }}</dd>
        <template v-if="s.llm_provider !== undefined">
          <dt>{{ t('settings.env.llmProvider') }}</dt>
          <dd>{{ show(s.llm_provider) }}</dd>
        </template>
        <dt>{{ t('settings.env.lmStudio') }}</dt>
        <dd>{{ show(s.lm_studio_base_url) }}</dd>
        <template v-if="s.leaving_notice_days !== undefined">
          <dt>{{ t('settings.env.leavingDays') }}</dt>
          <dd>{{ Array.isArray(s.leaving_notice_days) ? s.leaving_notice_days.join(', ') : show(s.leaving_notice_days) }}</dd>
        </template>
      </dl>
    </section>
    <section v-if="s.recommender" class="card">
      <h2>{{ t('settings.recommender') }}</h2>
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
section > p {
  margin: 0 0 8px;
}
section > p.after {
  margin: 10px 0 0;
}
#api-keys {
  scroll-margin-top: 16px;
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
@media (max-width: 480px) {
  dl {
    grid-template-columns: 1fr;
    gap: 0 0;
  }
  dd {
    margin-bottom: 6px;
  }
}
</style>
