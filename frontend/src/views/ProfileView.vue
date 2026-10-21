<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { api, type UserProfile } from '../api/client'
import { AVATAR_COLORS, isApiError, me, safeNext, selectProfile } from '../auth'
import ColorPalette from '../components/ColorPalette.vue'
import LanguageSwitch from '../components/LanguageSwitch.vue'
import ProfileAvatar from '../components/ProfileAvatar.vue'

const LOCK_SECONDS = 30

const { t } = useI18n()
const route = useRoute()
const next = computed(() => safeNext(route.query.next))

const users = ref<UserProfile[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

// Password step for one profile at a time.
const pending = ref<number | null>(null)
const password = ref('')
const pwError = ref<string | null>(null)
const busy = ref<number | null>(null)
const lockedUntil = reactive<Record<number, number>>({})
const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined

function secondsLeft(id: number): number {
  const until = lockedUntil[id]
  return until ? Math.max(0, Math.ceil((until - now.value) / 1000)) : 0
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    users.value = (await api.listUsers()).items
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function errorText(e: unknown): string {
  if (isApiError(e, 'wrong_password')) return t('profile.wrongPassword')
  return e instanceof Error ? e.message : String(e)
}

async function choose(u: UserProfile) {
  if (busy.value !== null) return
  if (u.has_password) {
    if (pending.value !== u.id) {
      pending.value = u.id
      password.value = ''
      pwError.value = null
      await nextTick()
      document.getElementById(`pw-${u.id}`)?.focus()
    }
    return
  }
  await enter(u.id, null)
}

async function enter(id: number, pw: string | null) {
  if (secondsLeft(id) > 0) return
  busy.value = id
  pwError.value = null
  try {
    await selectProfile(id, pw, next.value)
  } catch (e) {
    if (isApiError(e, 'too_many_attempts') || (isApiError(e) && e.status === 429)) {
      lockedUntil[id] = Date.now() + LOCK_SECONDS * 1000
      pwError.value = null
    } else {
      pwError.value = errorText(e)
    }
    password.value = ''
    busy.value = null
  }
}

function cancelPassword() {
  pending.value = null
  password.value = ''
  pwError.value = null
}

// New profile form
const creating = ref(false)
const form = reactive({ name: '', color: AVATAR_COLORS[0]!, password: '', confirm: '' })
const formError = ref<string | null>(null)
const saving = ref(false)
const firstProfile = computed(() => !loading.value && !loadError.value && users.value.length === 0)
/** Without a session the backend only allows creating the very first profile. */
const canCreate = computed(() => firstProfile.value || me.value !== null)

function openCreate() {
  creating.value = true
  form.name = ''
  form.color = AVATAR_COLORS[users.value.length % AVATAR_COLORS.length]!
  form.password = ''
  form.confirm = ''
  formError.value = null
  void nextTick(() => document.getElementById('new-name')?.focus())
}

async function create() {
  formError.value = null
  const name = form.name.trim()
  if (!name) {
    formError.value = t('profile.nameRequired')
    return
  }
  if (form.password !== form.confirm) {
    formError.value = t('profile.passwordMismatch')
    return
  }
  saving.value = true
  try {
    const created = await api.createUser({ name, avatar_color: form.color, password: form.password || null })
    // Enter the new profile right away (the first one has no session yet).
    await selectProfile(created.id, form.password || null, next.value)
  } catch (e) {
    formError.value = errorText(e)
    saving.value = false
    void load()
  }
}

onMounted(async () => {
  ticker = setInterval(() => (now.value = Date.now()), 1000)
  await load()
  if (firstProfile.value) openCreate()
})
onBeforeUnmount(() => clearInterval(ticker))
</script>

<template>
  <div class="picker">
    <div class="top">
      <span class="brand">{{ t('app.name') }}</span>
      <LanguageSwitch compact />
    </div>
    <h1>{{ firstProfile ? t('profile.welcome') : t('profile.who') }}</h1>

    <p v-if="loading" class="empty"><span class="spinner"></span> {{ t('common.loading') }}</p>
    <div v-else-if="loadError" class="card">
      <p class="small error" role="alert">{{ loadError }}</p>
      <button type="button" @click="load">{{ t('profile.retry') }}</button>
    </div>
    <template v-else>
      <p v-if="firstProfile" class="small muted intro">{{ t('profile.firstIntro') }}</p>
      <ul class="profiles">
        <li v-for="u in users" :key="u.id" class="card profile" :class="{ open: pending === u.id }">
          <button
            type="button"
            class="pick"
            :disabled="busy !== null"
            :aria-expanded="u.has_password ? pending === u.id : undefined"
            @click="choose(u)"
          >
            <ProfileAvatar :name="u.name" :color="u.avatar_color" :size="64" />
            <span class="name">
              {{ u.name }}
              <svg v-if="u.has_password" class="lock" width="14" height="14" viewBox="0 0 24 24" :aria-label="t('profile.hasPassword')" role="img">
                <path
                  d="M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linejoin="round"
                />
              </svg>
            </span>
            <span v-if="u.is_admin" class="tag">{{ t('profile.admin') }}</span>
            <span v-if="busy === u.id && !u.has_password" class="spinner" aria-hidden="true"></span>
          </button>
          <form v-if="pending === u.id" class="pw" @submit.prevent="enter(u.id, password)">
            <label :for="`pw-${u.id}`" class="small">{{ t('profile.password') }}</label>
            <input
              :id="`pw-${u.id}`"
              v-model="password"
              type="password"
              autocomplete="current-password"
              :disabled="busy === u.id || secondsLeft(u.id) > 0"
            />
            <p v-if="secondsLeft(u.id) > 0" class="small warn" role="status">
              {{ t('profile.tooMany', { n: secondsLeft(u.id) }) }}
            </p>
            <p v-else-if="pwError" class="small error" role="alert">{{ pwError }}</p>
            <div class="row">
              <button type="button" class="link small" @click="cancelPassword">{{ t('profile.cancel') }}</button>
              <button type="submit" class="primary" :disabled="!password || busy === u.id || secondsLeft(u.id) > 0">
                <span v-if="busy === u.id" class="spinner" aria-hidden="true"></span>
                {{ t('profile.enter') }}
              </button>
            </div>
          </form>
        </li>

        <li v-if="canCreate && !creating" class="card profile new">
          <button type="button" class="pick" @click="openCreate">
            <span class="plus" aria-hidden="true">+</span>
            <span class="name">{{ t('profile.new') }}</span>
          </button>
        </li>
      </ul>
      <p v-if="!canCreate" class="small muted">{{ t('profile.createNeedsSession') }}</p>

      <form v-if="creating" class="card create" @submit.prevent="create">
        <h2>{{ t('profile.new') }}</h2>
        <p v-if="firstProfile" class="small note">{{ t('profile.firstIsAdmin') }}</p>
        <label for="new-name" class="small">{{ t('profile.name') }}</label>
        <input id="new-name" v-model="form.name" type="text" maxlength="40" autocomplete="off" />
        <span class="small">{{ t('profile.color') }}</span>
        <div class="preview">
          <ProfileAvatar :name="form.name || '?'" :color="form.color" :size="36" />
          <ColorPalette v-model="form.color" :label="t('profile.color')" />
        </div>
        <label for="new-pw" class="small">{{ t('profile.passwordOptional') }}</label>
        <input id="new-pw" v-model="form.password" type="password" autocomplete="new-password" />
        <template v-if="form.password">
          <label for="new-pw2" class="small">{{ t('profile.passwordConfirm') }}</label>
          <input id="new-pw2" v-model="form.confirm" type="password" autocomplete="new-password" />
        </template>
        <p class="small muted">{{ t('profile.passwordHint') }}</p>
        <p v-if="formError" class="small error" role="alert">{{ formError }}</p>
        <div class="row">
          <button v-if="!firstProfile" type="button" :disabled="saving" @click="creating = false">{{ t('profile.cancel') }}</button>
          <button type="submit" class="primary" :disabled="saving || !form.name.trim()">
            <span v-if="saving" class="spinner" aria-hidden="true"></span>
            {{ t('profile.create') }}
          </button>
        </div>
      </form>
    </template>
  </div>
</template>

<style scoped>
.picker {
  max-width: 760px;
  margin: 0 auto;
  padding-top: 8px;
}
.top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.brand {
  font-weight: 700;
  font-size: 1.1rem;
}
h1 {
  text-align: center;
}
.intro {
  text-align: center;
  margin: -6px 0 16px;
}
.profiles {
  list-style: none;
  margin: 0 0 16px;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
}
.profile {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0;
}
.profile.open {
  border-color: var(--accent);
}
.pick {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 18px 10px 14px;
  border: none;
  background: none;
  border-radius: var(--radius);
}
.pick:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.name {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 600;
  overflow-wrap: anywhere;
  text-align: center;
}
.lock {
  color: var(--text-muted);
  flex: none;
}
.plus {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border: 2px dashed var(--border);
  border-radius: 50%;
  font-size: 2rem;
  color: var(--text-muted);
}
.pw {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0 10px 12px;
}
.pw p {
  margin: 0;
}
input[type='password'],
input[type='text'] {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}
.row {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.create {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 420px;
  margin: 0 auto;
}
.create h2 {
  font-size: 1.1rem;
  margin: 0 0 4px;
}
.create p {
  margin: 0;
}
.preview {
  display: flex;
  align-items: center;
  gap: 12px;
}
.note {
  padding: 6px 10px;
  border-left: 3px solid var(--accent);
  background: var(--surface-2);
  border-radius: var(--radius);
}
.error {
  color: var(--danger);
}
.warn {
  color: var(--warn);
}
@media (max-width: 480px) {
  .profiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
