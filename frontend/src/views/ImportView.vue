<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type ImportJob, type ImportSource, type NetflixProfile, type UserProfile } from '../api/client'
import { appSettings, loadAppSettings, loadSubscriptions, subscriptions } from '../appSettings'
import { me } from '../auth'
import FilePicker from '../components/FilePicker.vue'
import ProviderBadge from '../components/ProviderBadge.vue'
import { fmtDateTime } from '../format'
import { PROVIDER_BY_KEY } from '../providers'
import { pushToast } from '../toast'

const POLL_MS = 3000
const NETFLIX = 'netflix_export'
const PRIME = 'prime_video_export'
/** Sources whose export lists profiles: the upload asks which one to import. */
const PROFILE_SOURCES: Record<string, (file: File) => Promise<{ profiles: NetflixProfile[] }>> = {
  [NETFLIX]: (file) => api.netflixProfiles(file),
  [PRIME]: (file) => api.primeProfiles(file),
}

function hasProfiles(source: string): boolean {
  return source in PROFILE_SOURCES
}

/** The profile the settings already point at (Netflix: app setting; Prime: subscription row). */
function preferredProfile(source: string): string | null {
  if (source === NETFLIX) return appSettings.data?.netflix_profile_name ?? null
  if (source === PRIME) return subscriptions.items.find((s) => s.key === 'prime_video')?.selected_profile ?? 'ADULT'
  return null
}

const { t, te } = useI18n()
const sources = ref<ImportSource[]>([])
const sourcesLoading = ref(false)
const jobs = ref<ImportJob[]>([])
const jobsLoaded = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let unmounted = false

interface UploadState {
  file: File | null
  inputKey: number
  uploading: boolean
  profiles: NetflixProfile[] | null
  profilesLoading: boolean
  /** Export profile -> app profile id, or '' = do not import (M8/A `assign`). */
  assign: Record<string, number | ''>
}
const state = reactive<Record<string, UploadState>>({})

function st(source: string): UploadState {
  if (!state[source]) {
    state[source] = { file: null, inputKey: 0, uploading: false, profiles: null, profilesLoading: false, assign: {} }
  }
  return state[source]!
}

/** App profiles the export profiles can be assigned to (GET /users). */
const appUsers = ref<UserProfile[]>([])

async function loadUsers() {
  try {
    appUsers.value = (await api.listUsers()).items
  } catch {
    appUsers.value = []
  }
}

/** Options for the assignment selects: every app profile, the current one first. */
const userOptions = computed(() => {
  const cur = me.value
  const list = appUsers.value.length ? [...appUsers.value] : cur ? [{ ...cur }] : []
  return list.sort((a, b) => (a.id === cur?.id ? -1 : b.id === cur?.id ? 1 : a.name.localeCompare(b.name)))
})

function userLabel(u: { id: number; name: string }): string {
  return u.id === me.value?.id ? t('import.assignMe', { name: u.name }) : u.name
}

function assigned(source: string): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [name, uid] of Object.entries(st(source).assign)) if (typeof uid === 'number') out[name] = uid
  return out
}

const supported = computed(() => sources.value.filter((s) => s.supported))
const unsupported = computed(() => sources.value.filter((s) => !s.supported))

// Source-name prefixes that differ from the provider keys in providers.ts.
const PROVIDER_ALIASES: Record<string, string> = { apple_tv: 'apple_tv_plus' }

/** Provider key behind a source name ("disney_plus_export" -> "disney_plus"). */
function providerKeyOf(source: string): string | null {
  const stem = source.replace(/_(export|history|viewing|ratings|watchlist|mylist)$/, '')
  const base = PROVIDER_ALIASES[stem] ?? stem
  return PROVIDER_BY_KEY[base] ? base : null
}

/** label_key from the API is "import.source.<source>". */
function labelOf(src: ImportSource | string): string {
  const source = typeof src === 'string' ? src : src.source
  const key = typeof src === 'string' ? `import.source.${source}` : src.label_key
  if (key && te(key)) return t(key)
  const pk = providerKeyOf(source)
  return pk ? PROVIDER_BY_KEY[pk]!.name : source
}

function hintOf(source: string): string | null {
  return te(`import.hint.${source}`) ? t(`import.hint.${source}`) : null
}

/** note_key from the API is "import.unsupported.<source>"; unknown keys use the generic text. */
function noteOf(src: ImportSource): string {
  const pk = providerKeyOf(src.source)
  const provider = pk ? PROVIDER_BY_KEY[pk]!.name : labelOf(src)
  if (src.note_key && te(src.note_key)) return t(src.note_key, { provider })
  return t('import.unsupportedGeneric', { provider })
}

function isActive(j: ImportJob): boolean {
  return j.status === 'queued' || j.status === 'running'
}

async function loadSources() {
  sourcesLoading.value = true
  try {
    sources.value = await api.getImportSources()
  } catch {
    // toast already shown
  } finally {
    sourcesLoading.value = false
  }
}

async function loadJobs(quiet = false) {
  const wasActive = jobs.value.some(isActive)
  try {
    jobs.value = await api.getImportJobs(20, quiet)
    jobsLoaded.value = true
  } catch {
    // keep the last list
  }
  // a finished Netflix/Prime import may have added profiles to the settings
  if (wasActive && !jobs.value.some(isActive)) {
    void loadAppSettings()
    void loadSubscriptions(true)
  }
  schedulePoll()
}

function schedulePoll() {
  clearTimeout(timer)
  if (!unmounted && jobs.value.some(isActive)) timer = setTimeout(() => void loadJobs(true), POLL_MS)
}

async function onFile(source: string, f: File | null) {
  const s = st(source)
  s.file = f
  s.profiles = null
  const readProfiles = PROFILE_SOURCES[source]
  if (!s.file || !readProfiles) return
  s.profilesLoading = true
  try {
    const res = await readProfiles(s.file)
    s.profiles = res.profiles
    if (source === PRIME) void loadSubscriptions(true)
    const preferred = preferredProfile(source)
    const names = res.profiles.map((p) => p.name)
    // Default: the preferred (or first) export profile -> the current app profile, the rest skipped.
    const chosen = preferred && names.includes(preferred) ? preferred : (names[0] ?? '')
    s.assign = Object.fromEntries(names.map((n) => [n, n === chosen && me.value ? me.value.id : ''] as const))
  } catch {
    s.profiles = []
  } finally {
    s.profilesLoading = false
  }
}

function canUpload(source: string): boolean {
  const s = st(source)
  if (!s.file || s.uploading || s.profilesLoading) return false
  if (hasProfiles(source) && s.profiles && s.profiles.length > 0 && !Object.keys(assigned(source)).length) return false
  return true
}

async function upload(source: string) {
  const s = st(source)
  if (!s.file || !canUpload(source)) return
  s.uploading = true
  try {
    let profileName: string | null = null
    let assign: Record<string, number> | null = null
    if (hasProfiles(source) && s.profiles?.length) {
      assign = assigned(source)
      // profile_name stays for older job views: the one going to the current profile, else the first
      const mine = Object.keys(assign).find((n) => assign![n] === me.value?.id)
      profileName = mine ?? Object.keys(assign)[0] ?? null
    }
    const job = await api.uploadImport(source, s.file, profileName, assign)
    jobs.value = [job, ...jobs.value.filter((j) => j.id !== job.id)]
    pushToast(t('import.uploaded'))
    s.file = null
    s.profiles = null
    s.inputKey++
    schedulePoll()
  } catch {
    // toast already shown; keep the chosen file so it can be retried
  } finally {
    s.uploading = false
  }
}

function profileText(p: NetflixProfile): string {
  return p.primary_lang
    ? t('import.profileOption', { name: p.name, lang: p.primary_lang, rows: p.viewing_rows })
    : t('import.profileOptionNoLang', { name: p.name, rows: p.viewing_rows })
}

function assignText(a: Record<string, number>): string {
  return Object.entries(a)
    .map(([name, uid]) => `${name} → ${appUsers.value.find((u) => u.id === uid)?.name ?? `#${uid}`}`)
    .join(', ')
}

function statusText(status: string): string {
  return te(`import.jobs.status.${status}`) ? t(`import.jobs.status.${status}`) : status
}

function statusClass(status: string): string {
  if (status === 'done') return 'conf-verified'
  if (status === 'failed') return 'conf-conflicting'
  return 'conf-estimated'
}

const SUMMARY_KEYS = ['processed', 'created', 'updated', 'skipped', 'unresolved', 'ambiguous'] as const

function summaryParts(j: ImportJob): { key: string; value: number }[] {
  const sm = j.summary
  if (!sm) return []
  return SUMMARY_KEYS.flatMap((k) => {
    const v = sm[k]
    return typeof v === 'number' && (v > 0 || k === 'processed') ? [{ key: k, value: v }] : []
  })
}

function needsResolution(j: ImportJob): boolean {
  return (j.summary?.ambiguous ?? 0) > 0 || (j.summary?.unresolved ?? 0) > 0
}

onMounted(() => {
  void loadSources()
  void loadJobs()
  void loadUsers()
})
onBeforeUnmount(() => {
  unmounted = true
  clearTimeout(timer)
})
</script>

<template>
  <h1>{{ t('import.title') }}</h1>
  <p class="small muted">{{ t('import.intro') }}</p>

  <p v-if="sourcesLoading && !sources.length" class="empty"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <div v-else class="sources">
    <section v-for="src in supported" :key="src.source" class="card source">
      <h2>{{ labelOf(src) }}</h2>
      <p v-if="hintOf(src.source)" class="small muted">{{ hintOf(src.source) }}</p>
      <div class="pick-row">
        <FilePicker
          :key="st(src.source).inputKey"
          :model-value="st(src.source).file"
          :accept="src.accepts"
          :label="t('import.chooseFile', { ext: src.accepts })"
          :disabled="st(src.source).uploading"
          @update:model-value="onFile(src.source, $event)"
        />
      </div>
      <template v-if="hasProfiles(src.source) && st(src.source).file">
        <p v-if="st(src.source).profilesLoading" class="small muted"><span class="spinner"></span> {{ t('import.readingProfiles') }}</p>
        <p v-else-if="st(src.source).profiles && !st(src.source).profiles!.length" class="small warn">{{ t('import.noProfiles') }}</p>
        <fieldset v-else-if="st(src.source).profiles" class="assign">
          <legend class="small">{{ src.source === PRIME ? t('import.assignPrime') : t('import.assignNetflix') }}</legend>
          <p class="small muted">{{ t('import.assignHint') }}</p>
          <label v-for="p in st(src.source).profiles" :key="p.name" class="assign-row">
            <span class="small">{{ profileText(p) }}</span>
            <select v-model="st(src.source).assign[p.name]">
              <option v-for="u in userOptions" :key="u.id" :value="u.id">{{ userLabel(u) }}</option>
              <option value="">{{ t('import.assignSkip') }}</option>
            </select>
          </label>
          <p v-if="!Object.keys(assigned(src.source)).length" class="small warn">{{ t('import.assignNone') }}</p>
        </fieldset>
      </template>
      <div>
        <button type="button" class="primary" :disabled="!canUpload(src.source)" @click="upload(src.source)">
          <span v-if="st(src.source).uploading" class="spinner" aria-hidden="true"></span>
          {{ st(src.source).uploading ? t('import.uploading') : t('import.upload') }}
        </button>
      </div>
    </section>

    <section v-for="src in unsupported" :key="src.source" class="card source disabled" aria-disabled="true">
      <h2>
        <ProviderBadge v-if="providerKeyOf(src.source)" :provider-key="providerKeyOf(src.source)!" />
        <span v-else>{{ labelOf(src) }}</span>
        <span class="tag">{{ t('import.notSupported') }}</span>
      </h2>
      <p class="small">{{ noteOf(src) }}</p>
    </section>
  </div>

  <h2 class="jobs-title">{{ t('import.jobs.title') }}</h2>
  <p v-if="!jobsLoaded" class="empty"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <p v-else-if="!jobs.length" class="small muted">{{ t('import.jobs.empty') }}</p>
  <ul v-else class="jobs">
    <li v-for="j in jobs" :key="j.id" class="card job">
      <div class="job-head">
        <strong>{{ labelOf(j.source) }}</strong>
        <span class="tag" :class="statusClass(j.status)">
          <span v-if="isActive(j)" class="spinner tiny" aria-hidden="true"></span>
          {{ statusText(j.status) }}
        </span>
        <span class="small muted file-name">{{ j.file_name }}</span>
      </div>
      <p class="small muted meta">
        <span v-if="j.started_at">{{ t('import.jobs.started', { date: fmtDateTime(j.started_at) }) }}</span>
        <span v-if="j.finished_at">{{ t('import.jobs.finished', { date: fmtDateTime(j.finished_at) }) }}</span>
        <span v-if="j.params?.assign && Object.keys(j.params.assign).length">
          {{ t('import.jobs.assigned', { list: assignText(j.params.assign) }) }}
        </span>
        <span v-else-if="j.params?.profile_name">{{ t('import.jobs.profile', { name: j.params.profile_name }) }}</span>
      </p>
      <p v-if="summaryParts(j).length" class="small summary">
        <span v-for="p in summaryParts(j)" :key="p.key" class="chip">{{ t(`import.jobs.summary.${p.key}`) }}: {{ p.value }}</span>
      </p>
      <p v-if="j.error" class="small error">{{ j.error }}</p>
      <RouterLink v-if="needsResolution(j)" to="/feloldas" class="small">{{ t('import.jobs.resolve') }} →</RouterLink>
    </li>
  </ul>
</template>

<style scoped>
.sources {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}
@media (max-width: 400px) {
  .sources {
    grid-template-columns: 1fr;
  }
}
.source {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.source h2 {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 1.05rem;
}
.source p {
  margin: 0;
}
.source.disabled {
  opacity: 0.6;
  box-shadow: none;
  background: var(--surface-2);
}
.pick-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
}
.assign {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.assign legend {
  padding: 0 4px;
  font-weight: 600;
}
.assign-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  align-items: center;
  gap: 4px 8px;
}
.assign-row span {
  overflow-wrap: anywhere;
}
@media (max-width: 400px) {
  .assign-row {
    grid-template-columns: 1fr;
  }
}
.jobs-title {
  font-size: 1.15rem;
  margin: 0 0 10px;
}
.jobs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.job {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.job p {
  margin: 0;
}
.job-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
}
.file-name {
  overflow-wrap: anywhere;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 12px;
}
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.error {
  color: var(--danger);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.warn {
  color: var(--warn);
}
.spinner.tiny {
  width: 10px;
  height: 10px;
  border-width: 2px;
}
</style>
