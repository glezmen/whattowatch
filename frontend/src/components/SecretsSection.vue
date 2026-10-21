<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError, api, type SecretItem } from '../api/client'
import { pushToast } from '../toast'

const emit = defineEmits<{ saved: [] }>()
const { t, te } = useI18n()

const HELP: Record<string, string> = {
  TMDB_API_KEY: 'https://www.themoviedb.org/settings/api',
  STREAMING_AVAILABILITY_API_KEY: 'https://www.movieofthenight.com/about/api',
  ANTHROPIC_API_KEY: 'https://console.anthropic.com/',
  OPENAI_API_KEY: 'https://platform.openai.com/api-keys',
}

const items = ref<SecretItem[]>([])
const loaded = ref(false)
const failed = ref(false)
const drafts = reactive<Record<string, string>>({})
const busy = reactive<Record<string, boolean>>({})
const errors = reactive<Record<string, string>>({})
const baseUrl = ref('')
const baseUrlSaved = ref('')

async function load() {
  try {
    const res = await api.getSecrets()
    apply(res.items, res.lm_studio_base_url)
    failed.value = false
  } catch {
    failed.value = true
  } finally {
    loaded.value = true
  }
}

function apply(list: SecretItem[], url: string | undefined) {
  items.value = list
  if (url !== undefined) {
    baseUrl.value = url ?? ''
    baseUrlSaved.value = url ?? ''
  }
}

onMounted(load)

function label(s: SecretItem): string {
  if (s.label_key && te(s.label_key)) return t(s.label_key)
  const k = `settings.secrets.label.${s.key}`
  return te(k) ? t(k) : s.key
}

function helpText(key: string): string | null {
  const k = `settings.secrets.help.${key}`
  return te(k) ? t(k) : null
}

function errorFor(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === 'invalid_tmdb_key') return t('settings.secrets.invalidTmdb')
    return e.message
  }
  return t('settings.secrets.saveFailed')
}

async function save(key: string, value: string | null) {
  busy[key] = true
  delete errors[key]
  try {
    const res = await api.putSecrets({ values: { [key]: value } })
    apply(res.items, res.lm_studio_base_url)
    drafts[key] = ''
    pushToast(value === null ? t('settings.secrets.cleared') : t('settings.secrets.saved'))
    emit('saved')
  } catch (e) {
    errors[key] = errorFor(e)
  } finally {
    busy[key] = false
  }
}

async function saveBaseUrl() {
  busy.lm_studio_base_url = true
  delete errors.lm_studio_base_url
  try {
    const res = await api.putSecrets({ lm_studio_base_url: baseUrl.value.trim() })
    apply(res.items, res.lm_studio_base_url)
    pushToast(t('settings.saved'))
    emit('saved')
  } catch (e) {
    errors.lm_studio_base_url = errorFor(e)
  } finally {
    busy.lm_studio_base_url = false
  }
}
</script>

<template>
  <p class="small muted">{{ t('settings.secrets.hint') }}</p>
  <p v-if="!loaded" class="small muted"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <p v-else-if="failed" class="small muted">{{ t('settings.secrets.loadFailed') }}</p>
  <ul v-else class="secrets">
    <li v-for="s in items" :key="s.key">
      <div class="head">
        <label :for="`secret-${s.key}`" class="name">{{ label(s) }}</label>
        <span class="chip" :class="s.set ? 'set' : 'unset'">
          <template v-if="s.set">{{ t('settings.secrets.set') }}<template v-if="s.hint"> ····{{ s.hint }}</template></template>
          <template v-else>{{ t('settings.secrets.unset') }}</template>
        </span>
      </div>
      <p v-if="helpText(s.key) || HELP[s.key]" class="small muted help">
        {{ helpText(s.key) }}
        <a v-if="HELP[s.key]" :href="HELP[s.key]" target="_blank" rel="noopener noreferrer">{{ HELP[s.key].replace(/^https:\/\/(www\.)?/, '').replace(/\/$/, '') }}</a>
      </p>
      <form class="controls" @submit.prevent="drafts[s.key]?.trim() && save(s.key, drafts[s.key].trim())">
        <input
          :id="`secret-${s.key}`"
          v-model="drafts[s.key]"
          type="password"
          autocomplete="off"
          spellcheck="false"
          :placeholder="s.set ? t('settings.secrets.replacePlaceholder') : t('settings.secrets.placeholder')"
        />
        <button type="submit" class="primary" :disabled="busy[s.key] || !drafts[s.key]?.trim()">
          <span v-if="busy[s.key]" class="spinner" aria-hidden="true"></span>
          {{ t('common.save') }}
        </button>
        <button type="button" :disabled="busy[s.key] || !s.set" @click="save(s.key, null)">{{ t('common.delete') }}</button>
      </form>
      <p v-if="errors[s.key]" class="small error" role="alert">{{ errors[s.key] }}</p>
    </li>
    <li>
      <div class="head">
        <label for="lm-studio-url" class="name">{{ t('settings.secrets.lmStudioUrl') }}</label>
      </div>
      <p class="small muted help">{{ t('settings.secrets.lmStudioUrlHelp') }}</p>
      <form class="controls" @submit.prevent="saveBaseUrl">
        <input id="lm-studio-url" v-model="baseUrl" type="text" inputmode="url" placeholder="http://localhost:1234/v1" />
        <button
          type="submit"
          class="primary"
          :disabled="busy.lm_studio_base_url || !baseUrl.trim() || baseUrl.trim() === baseUrlSaved"
        >
          <span v-if="busy.lm_studio_base_url" class="spinner" aria-hidden="true"></span>
          {{ t('common.save') }}
        </button>
      </form>
      <p v-if="errors.lm_studio_base_url" class="small error" role="alert">{{ errors.lm_studio_base_url }}</p>
    </li>
  </ul>
</template>

<style scoped>
.secrets {
  list-style: none;
  margin: 0;
  padding: 0;
}
.secrets li {
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.secrets li:last-child {
  border-bottom: none;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
}
.name {
  font-weight: 600;
}
.chip.set {
  color: var(--positive);
}
.chip.unset {
  color: var(--text-muted);
}
.help {
  margin: 2px 0 6px;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
  align-items: center;
}
.controls input {
  flex: 1 1 220px;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}
.error {
  color: var(--danger);
  margin: 4px 0 0;
}
</style>
