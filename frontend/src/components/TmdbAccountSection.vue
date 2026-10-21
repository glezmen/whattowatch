<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError, api, type TmdbAccountStatus } from '../api/client'
import { me } from '../auth'
import { pushToast } from '../toast'
import ToggleSwitch from './ToggleSwitch.vue'

const props = defineProps<{ reloadToken?: number }>()
const { t } = useI18n()

const st = ref<TmdbAccountStatus | null>(null)
const failed = ref(false)
const busy = ref(false)
const awaiting = ref(false)
const error = ref('')
const resyncResult = ref<{ ok: number; error: number; skipped: number } | null>(null)

async function load() {
  try {
    st.value = await api.getTmdbAccount()
    awaiting.value = awaiting.value && !st.value.linked
    failed.value = false
  } catch {
    failed.value = true
  }
}
onMounted(load)
watch(() => props.reloadToken, load)

function msg(e: unknown): string {
  return e instanceof ApiError ? e.message : t('settings.tmdb.failed')
}

async function start() {
  // Open the tab synchronously (popup blockers), set its location once the URL is known.
  const win = window.open('about:blank', '_blank')
  busy.value = true
  error.value = ''
  try {
    const { approval_url } = await api.tmdbLinkStart()
    if (win) {
      win.opener = null
      win.location.href = approval_url
    } else {
      window.open(approval_url, '_blank', 'noopener,noreferrer')
    }
    awaiting.value = true
  } catch (e) {
    win?.close()
    error.value = e instanceof ApiError && e.code === 'no_tmdb_key' ? t('settings.tmdb.noKey') : msg(e)
  } finally {
    busy.value = false
  }
}

async function finish() {
  busy.value = true
  error.value = ''
  try {
    st.value = await api.tmdbLinkFinish()
    awaiting.value = false
    pushToast(t('settings.tmdb.linkedToast'))
  } catch (e) {
    error.value = e instanceof ApiError && e.code === 'tmdb_link_failed' ? `${e.message} ${t('settings.tmdb.approveFirst')}` : msg(e)
  } finally {
    busy.value = false
  }
}

async function unlink() {
  if (!window.confirm(t('settings.tmdb.unlinkConfirm'))) return
  busy.value = true
  error.value = ''
  try {
    st.value = await api.tmdbUnlink()
    awaiting.value = false
    resyncResult.value = null
  } catch (e) {
    error.value = msg(e)
  } finally {
    busy.value = false
  }
}

async function setSync(v: boolean) {
  if (!st.value) return
  const prev = st.value.rating_sync_enabled
  st.value = { ...st.value, rating_sync_enabled: v }
  error.value = ''
  try {
    st.value = await api.putTmdbAccount(v)
  } catch (e) {
    st.value = { ...st.value, rating_sync_enabled: prev }
    error.value = msg(e)
  }
}

async function resync() {
  busy.value = true
  error.value = ''
  resyncResult.value = null
  try {
    resyncResult.value = await api.tmdbResync()
  } catch (e) {
    error.value = msg(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <p v-if="!st && !failed" class="small muted"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <p v-else-if="!st" class="small muted">{{ t('settings.tmdb.loadFailed') }}</p>
  <div v-else class="tmdb">
    <p class="status">
      <span v-if="st.linked" class="chip linked">{{ t('settings.tmdb.linked', { name: st.account?.username ?? '?' }) }}</span>
      <span v-else class="chip">{{ t('settings.tmdb.notLinked') }}</span>
    </p>
    <p v-if="!st.api_key_set" class="small muted">
      {{ t('settings.tmdb.needKey') }}
      <a v-if="me?.is_admin" href="#api-keys">{{ t('settings.tmdb.toKeys') }} →</a>
    </p>

    <div class="row">
      <template v-if="!st.linked">
        <button type="button" class="primary" :disabled="busy || !st.api_key_set" @click="start">
          <span v-if="busy && !awaiting" class="spinner" aria-hidden="true"></span>
          {{ t('settings.tmdb.link') }}
        </button>
        <button v-if="awaiting || st.pending" type="button" :disabled="busy" @click="finish">
          {{ t('settings.tmdb.finish') }}
        </button>
      </template>
      <button v-else type="button" :disabled="busy" @click="unlink">{{ t('settings.tmdb.unlink') }}</button>
    </div>
    <p v-if="(awaiting || st.pending) && !st.linked" class="small muted">{{ t('settings.tmdb.approveHint') }}</p>
    <p v-if="error" class="small error" role="alert">{{ error }}</p>

    <div class="row">
      <ToggleSwitch
        :model-value="st.linked && st.rating_sync_enabled"
        :disabled="!st.linked"
        :label="t('settings.tmdb.sync')"
        :hint="st.linked ? undefined : t('settings.tmdb.syncNeedLink')"
        @update:model-value="setSync"
      />
    </div>

    <div class="row">
      <button type="button" :disabled="busy || !st.linked" @click="resync">
        <span v-if="busy && st.linked" class="spinner" aria-hidden="true"></span>
        {{ t('settings.tmdb.resync') }}
      </button>
      <span v-if="resyncResult" class="small" role="status">
        {{ t('settings.tmdb.resyncResult', resyncResult) }}
      </span>
    </div>

    <p class="small muted">{{ t('settings.tmdb.perProfile') }}</p>
    <p class="small muted">{{ t('settings.tmdb.privacy') }}</p>
  </div>
</template>

<style scoped>
.tmdb {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tmdb p {
  margin: 0;
}
.row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
  align-items: center;
}
.chip.linked {
  color: var(--positive);
}
.error {
  color: var(--danger);
}
</style>
