<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type LlmProviderName, type LlmSettings, type LlmSettingsPatch, type LlmTestResult } from '../api/client'
import { me } from '../auth'
import { pushToast } from '../toast'
import ToggleSwitch from './ToggleSwitch.vue'

const props = defineProps<{ reloadToken?: number }>()
const { t } = useI18n()

const CUSTOM = '__custom__'
const PROVIDERS: LlmProviderName[] = ['lmstudio', 'anthropic', 'openai', 'claude_code']

const s = ref<LlmSettings | null>(null)
const loaded = ref(false)
const saving = ref(false)
const customModel = ref('')
const customMode = ref(false)
const testing = ref(false)
const testResult = ref<LlmTestResult | null>(null)

async function load() {
  try {
    s.value = await api.getLlmSettings()
    syncModelUi()
  } catch {
    // toast already shown
  } finally {
    loaded.value = true
  }
}

onMounted(load)
watch(
  () => props.reloadToken,
  () => void load(),
)

const models = computed<string[]>(() => {
  if (!s.value) return []
  return s.value.options[s.value.provider]?.models ?? []
})

/** Anthropic: static list + free text; LM Studio: /v1/models list (free text when unavailable or empty); OpenAI: free text. */
const usesSelect = computed(() => {
  if (!s.value) return false
  if (s.value.provider === 'openai') return false
  if (s.value.provider === 'lmstudio') return s.value.options.lmstudio.available && models.value.length > 0
  return true
})

function syncModelUi() {
  const cur = s.value?.model ?? ''
  customModel.value = cur
  customMode.value = !!cur && usesSelect.value && !models.value.includes(cur)
}

const selectValue = computed(() => (customMode.value ? CUSTOM : (s.value?.model ?? '')))

const keyMissing = computed(() => {
  if (!s.value) return false
  if (s.value.provider === 'anthropic') return !s.value.options.anthropic.key_set
  if (s.value.provider === 'openai') return !s.value.options.openai.key_set
  return false
})

const isCloud = computed(() => s.value !== null && s.value.provider !== 'lmstudio' && s.value.provider !== 'claude_code')

async function save(patch: LlmSettingsPatch) {
  if (!s.value) return
  const prev = { ...s.value }
  Object.assign(s.value, patch)
  saving.value = true
  try {
    const res = await api.putLlmSettings(patch)
    if (res && typeof res === 'object' && 'provider' in res) s.value = res
    syncModelUi()
    pushToast(t('settings.saved'))
  } catch {
    s.value = prev
    syncModelUi()
  } finally {
    saving.value = false
  }
}

function onProvider(e: Event) {
  const provider = (e.target as HTMLSelectElement).value as LlmProviderName
  if (!s.value || provider === s.value.provider) return
  const list = s.value.options[provider]?.models ?? []
  testResult.value = null
  customMode.value = false
  void save({ provider, model: provider === 'claude_code' ? null : (list[0] ?? null) })
}

function onModelSelect(e: Event) {
  const v = (e.target as HTMLSelectElement).value
  if (v === CUSTOM) {
    customMode.value = true
    customModel.value = ''
    return
  }
  customMode.value = false
  void save({ model: v || null })
}

function onModelText() {
  const v = customModel.value.trim()
  if (!s.value || v === (s.value.model ?? '')) return
  void save({ model: v || null })
}

async function runTest() {
  if (!s.value) return
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await api.testLlm({ provider: s.value.provider, model: s.value.model })
  } catch (e) {
    testResult.value = { ok: false, error: e instanceof Error ? e.message : String(e) }
  } finally {
    testing.value = false
  }
}

function usageText(u: Record<string, number> | null | undefined): string {
  if (!u) return ''
  return Object.entries(u)
    .map(([k, v]) => `${k}: ${v}`)
    .join(', ')
}
</script>

<template>
  <p v-if="!loaded" class="small muted"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <p v-else-if="!s" class="small muted">{{ t('settings.llm.loadFailed') }}</p>
  <div v-else class="llm">
    <div class="field">
      <label for="llm-provider">{{ t('settings.llm.provider') }}</label>
      <select id="llm-provider" :value="s.provider" :disabled="saving" @change="onProvider">
        <option v-for="p in PROVIDERS" :key="p" :value="p">{{ t(`settings.llm.providers.${p}`) }}</option>
      </select>
    </div>

    <div class="field">
      <label :for="usesSelect ? 'llm-model' : 'llm-model-text'">{{ t('settings.llm.model') }}</label>
      <div class="model">
        <select v-if="usesSelect" id="llm-model" :value="selectValue" :disabled="saving" @change="onModelSelect">
          <option v-if="s.provider === 'claude_code'" value="">{{ t('settings.llm.modelCliDefault') }}</option>
          <option v-else-if="!s.model" value="">{{ t('settings.llm.modelDefault') }}</option>
          <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
          <option :value="CUSTOM">{{ t('settings.llm.modelCustom') }}</option>
        </select>
        <input
          v-if="!usesSelect || customMode"
          id="llm-model-text"
          v-model="customModel"
          type="text"
          spellcheck="false"
          :placeholder="t('settings.llm.modelPlaceholder')"
          :aria-label="t('settings.llm.modelCustom')"
          @change="onModelText"
          @keydown.enter.prevent="onModelText"
        />
      </div>
    </div>

    <p v-if="s.provider === 'lmstudio' && !s.options.lmstudio.available" class="small banner warn" role="status">
      {{ t('settings.llm.lmUnavailable', { url: s.options.lmstudio.base_url }) }}
    </p>
    <p v-else-if="s.provider === 'lmstudio'" class="small muted">
      {{ t('settings.llm.lmAvailable', { url: s.options.lmstudio.base_url, n: models.length }) }}
    </p>
    <template v-if="s.provider === 'claude_code'">
      <p v-if="s.options.claude_code.available" class="small muted">
        {{ t('settings.llm.ccInstalled', { version: s.options.claude_code.version ?? '?' }) }}
      </p>
      <p v-else class="small banner warn" role="status">{{ t('settings.llm.ccUnavailable') }}</p>
      <p class="small muted">{{ t('settings.llm.ccPrivacy') }}</p>
    </template>
    <p v-if="keyMissing" class="small banner warn" role="status">
      {{ t('settings.llm.keyMissing', { provider: t(`settings.llm.providers.${s.provider}`) }) }}
      <a v-if="me?.is_admin" href="#api-keys">{{ t('settings.llm.keyMissingLink') }}</a>
    </p>
    <p v-if="isCloud" class="small muted">{{ t('settings.llm.cloudNote') }}</p>

    <ToggleSwitch
      :model-value="s.cloud_send_feedback_text"
      :disabled="saving"
      :label="t('settings.llm.sendFeedback')"
      :hint="t('settings.llm.sendFeedbackHint')"
      @update:model-value="(v) => save({ cloud_send_feedback_text: v })"
    />
    <ToggleSwitch
      :model-value="s.fallback_allowed"
      :disabled="saving"
      :label="t('settings.llm.fallback')"
      :hint="t('settings.llm.fallbackHint')"
      @update:model-value="(v) => save({ fallback_allowed: v })"
    />

    <div class="test">
      <button type="button" :disabled="testing || saving" @click="runTest">
        <span v-if="testing" class="spinner" aria-hidden="true"></span>
        {{ t('settings.llm.test') }}
      </button>
      <span class="small muted">{{ t('settings.llm.testHint') }}</span>
    </div>
    <div v-if="testResult" class="result small" :class="testResult.ok ? 'ok' : 'fail'" role="status">
      <template v-if="testResult.ok">
        <strong>{{ t('settings.llm.testOk') }}</strong>
        <dl>
          <dt>{{ t('settings.llm.model') }}</dt>
          <dd>{{ testResult.model || '–' }}</dd>
          <dt>{{ t('settings.llm.reply') }}</dt>
          <dd>{{ testResult.reply || '–' }}</dd>
          <template v-if="testResult.usage">
            <dt>{{ t('settings.llm.usage') }}</dt>
            <dd>{{ usageText(testResult.usage) }}</dd>
          </template>
        </dl>
      </template>
      <template v-else>
        <strong>{{ t('settings.llm.testFailed') }}</strong>
        <span v-if="testResult.error"> {{ testResult.error }}</span>
      </template>
    </div>
  </div>
</template>

<style scoped>
.llm {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.field {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
}
.field > label {
  min-width: 110px;
  color: var(--text-muted);
}
.model {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
  flex: 1 1 240px;
  min-width: 0;
}
.model select,
.model input {
  flex: 1 1 200px;
  min-width: 0;
}
.banner.warn {
  margin: 0;
}
p {
  margin: 0;
}
.test {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 12px;
}
.result {
  padding: 8px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface-2);
}
.result.ok {
  border-color: var(--positive);
}
.result.fail {
  border-color: var(--danger);
}
.result dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px 12px;
  margin: 6px 0 0;
}
.result dt {
  color: var(--text-muted);
}
.result dd {
  margin: 0;
  word-break: break-word;
  white-space: pre-wrap;
}
</style>
