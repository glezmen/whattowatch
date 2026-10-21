<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError, api, type BackupInfo, type BackupItem, type RestoreResult } from '../api/client'
import FilePicker from './FilePicker.vue'
import { fmtDateTime, fmtInt } from '../format'
import { pushToast } from '../toast'

const emit = defineEmits<{ restored: [] }>()
const { t } = useI18n()

const CONFIRM_WORD = 'RESTORE'

const backups = ref<BackupItem[]>([])
const backupsDir = ref('')
const note = ref('')
const listLoaded = ref(false)
const listFailed = ref(false)

const file = ref<File | null>(null)
const info = ref<BackupInfo | null>(null)
const inspecting = ref(false)
const restoring = ref(false)
const error = ref('')
const result = ref<RestoreResult | null>(null)

const dialog = ref<HTMLDialogElement | null>(null)
const confirmText = ref('')
const dialogError = ref('')

const timers: ReturnType<typeof setTimeout>[] = []

async function loadList() {
  try {
    const res = await api.listBackups()
    backups.value = res.items
    backupsDir.value = res.backups_dir
    if (res.note) note.value = res.note
    listFailed.value = false
  } catch {
    listFailed.value = true
  } finally {
    listLoaded.value = true
  }
}

onMounted(loadList)
onBeforeUnmount(() => timers.forEach(clearTimeout))

/** The download is a plain navigation; re-read the list once the snapshot has likely been written. */
function onExport() {
  for (const ms of [1500, 5000]) timers.push(setTimeout(loadList, ms))
}

function fmtSize(n?: number | null): string {
  if (n === null || n === undefined) return '–'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function errorFor(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === 'invalid_backup') return t('settings.backup.invalidBackup')
    if (e.code === 'confirm_required') return t('settings.backup.confirmRequired')
    return e.message
  }
  return t('settings.backup.failed')
}

watch(file, () => {
  info.value = null
  error.value = ''
  result.value = null
})

async function inspect() {
  if (!file.value) return
  inspecting.value = true
  error.value = ''
  info.value = null
  try {
    info.value = await api.inspectBackup(file.value)
    if (info.value.note) note.value = info.value.note
  } catch (e) {
    error.value = errorFor(e)
  } finally {
    inspecting.value = false
  }
}

function openConfirm() {
  confirmText.value = ''
  dialogError.value = ''
  dialog.value?.showModal()
}

function closeConfirm() {
  dialog.value?.close()
}

async function restore() {
  if (!file.value || confirmText.value !== CONFIRM_WORD) return
  restoring.value = true
  dialogError.value = ''
  try {
    result.value = await api.restoreBackup(file.value, confirmText.value)
    if (result.value.note) note.value = result.value.note
    closeConfirm()
    file.value = null
    info.value = null
    pushToast(t('settings.backup.restored'))
    void loadList()
    emit('restored')
  } catch (e) {
    dialogError.value = errorFor(e)
  } finally {
    restoring.value = false
  }
}
</script>

<template>
  <p class="small muted">{{ t('settings.backup.hint') }}</p>
  <p class="small note" role="note">{{ note || t('settings.backup.notIncluded') }}</p>

  <h3>{{ t('settings.backup.exportTitle') }}</h3>
  <div class="controls">
    <a class="button primary" :href="api.backupExportUrl" download @click="onExport">{{ t('settings.backup.download') }}</a>
    <span v-if="backupsDir" class="small muted">{{ t('settings.backup.savedIn', { dir: backupsDir }) }}</span>
  </div>
  <p v-if="!listLoaded" class="small muted"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <p v-else-if="listFailed" class="small muted">{{ t('settings.backup.listFailed') }}</p>
  <p v-else-if="!backups.length" class="small muted">{{ t('settings.backup.none') }}</p>
  <div v-else class="table-wrap">
    <table class="small">
      <thead>
        <tr>
          <th scope="col">{{ t('settings.backup.name') }}</th>
          <th scope="col">{{ t('settings.backup.size') }}</th>
          <th scope="col">{{ t('settings.backup.created') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="b in backups" :key="b.name">
          <td class="mono">{{ b.name }}</td>
          <td>{{ fmtSize(b.size_bytes) }}</td>
          <td>{{ fmtDateTime(b.created_at) }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <h3>{{ t('settings.backup.restoreTitle') }}</h3>
  <p class="small muted">{{ t('settings.backup.restoreHint') }}</p>
  <div class="controls">
    <FilePicker v-model="file" accept=".db" :label="t('filePicker.choose')" :disabled="inspecting || restoring" />
    <button type="button" :disabled="!file || inspecting || restoring" @click="inspect">
      <span v-if="inspecting" class="spinner" aria-hidden="true"></span>
      {{ t('settings.backup.inspect') }}
    </button>
  </div>
  <p v-if="error" class="small error" role="alert">{{ error }}</p>

  <div v-if="info" class="inspect">
    <dl>
      <dt>{{ t('settings.backup.revision') }}</dt>
      <dd class="mono">{{ info.alembic_revision ?? '–' }}</dd>
      <dt>{{ t('settings.backup.tables') }}</dt>
      <dd>{{ fmtInt(info.tables) }}</dd>
      <dt>{{ t('settings.backup.titles') }}</dt>
      <dd>{{ fmtInt(info.titles) }}</dd>
      <dt>{{ t('settings.backup.watchEvents') }}</dt>
      <dd>{{ fmtInt(info.watch_events) }}</dd>
      <dt>{{ t('settings.backup.size') }}</dt>
      <dd>{{ fmtSize(info.size_bytes) }}</dd>
    </dl>
    <button type="button" class="danger-btn" :disabled="restoring" @click="openConfirm">
      {{ t('settings.backup.restore') }}
    </button>
  </div>

  <div v-if="result" class="result" role="status">
    <p class="small"><strong>{{ t('settings.backup.restoredTitle') }}</strong></p>
    <dl class="small">
      <dt>{{ t('settings.backup.previousBackup') }}</dt>
      <dd class="mono">{{ result.previous_backup ?? t('settings.backup.noPrevious') }}</dd>
      <dt>{{ t('settings.backup.revisionBefore') }}</dt>
      <dd class="mono">{{ result.revision_before ?? '–' }}</dd>
      <dt>{{ t('settings.backup.revisionAfter') }}</dt>
      <dd class="mono">{{ result.revision_after ?? '–' }}</dd>
    </dl>
  </div>

  <dialog ref="dialog" class="confirm" aria-labelledby="restore-dialog-title" @cancel="restoring && $event.preventDefault()">
    <form method="dialog" @submit.prevent="restore">
      <h3 id="restore-dialog-title">{{ t('settings.backup.confirmTitle') }}</h3>
      <p class="small">{{ t('settings.backup.confirmBody') }}</p>
      <p class="small note">{{ note || t('settings.backup.notIncluded') }}</p>
      <label for="restore-confirm" class="small">{{ t('settings.backup.confirmLabel', { word: CONFIRM_WORD }) }}</label>
      <input
        id="restore-confirm"
        v-model="confirmText"
        type="text"
        autocomplete="off"
        spellcheck="false"
        :placeholder="CONFIRM_WORD"
      />
      <p v-if="dialogError" class="small error" role="alert">{{ dialogError }}</p>
      <div class="controls end">
        <button type="button" :disabled="restoring" @click="closeConfirm">{{ t('settings.backup.cancel') }}</button>
        <button type="submit" class="danger-btn" :disabled="restoring || confirmText !== CONFIRM_WORD">
          <span v-if="restoring" class="spinner" aria-hidden="true"></span>
          {{ t('settings.backup.restoreConfirm') }}
        </button>
      </div>
    </form>
  </dialog>
</template>

<style scoped>
h3 {
  font-size: 1rem;
  margin: 14px 0 6px;
}
.note {
  margin: 4px 0 8px;
  padding: 6px 10px;
  border-left: 3px solid var(--warn);
  background: var(--surface-2);
  border-radius: var(--radius);
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: center;
  margin-bottom: 8px;
}
.controls.end {
  justify-content: flex-end;
  margin: 12px 0 0;
}
a.button {
  display: inline-block;
  padding: 6px 12px;
  border-radius: var(--radius);
  text-decoration: none;
}
a.button.primary {
  background: var(--accent);
  color: var(--accent-text);
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-all;
}
th {
  color: var(--text-muted);
  font-weight: 400;
  text-align: left;
}
.inspect,
.result {
  margin-top: 8px;
}
dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px 16px;
  margin: 0 0 10px;
}
dt {
  color: var(--text-muted);
}
dd {
  margin: 0;
}
.danger-btn {
  border-color: var(--danger);
  color: var(--danger);
  font-weight: 600;
}
.danger-btn[type='submit']:not(:disabled) {
  background: var(--danger);
  color: #fff;
}
.error {
  color: var(--danger);
  margin: 4px 0 0;
}
.confirm {
  max-width: min(480px, calc(100vw - 32px));
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  box-shadow: var(--shadow);
}
.confirm::backdrop {
  background: rgb(0 0 0 / 0.45);
}
.confirm h3 {
  margin-top: 0;
  color: var(--danger);
}
.confirm input {
  display: block;
  width: 100%;
  box-sizing: border-box;
  margin-top: 4px;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
}
</style>
