<script setup lang="ts">
// Accounts mode: self-registration (POST /auth/register). Public route. The first account
// becomes the family admin. A signed-in admin adds a member here via POST /users instead,
// so their own session is kept.
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import {
  accountsMode,
  authStatus,
  AVATAR_COLORS,
  isApiError,
  LOGIN_PATH,
  me,
  MIN_PASSWORD_LENGTH,
  PROFILE_PATH,
  register,
  safeNext,
} from '../auth'
import ColorPalette from '../components/ColorPalette.vue'
import LanguageSwitch from '../components/LanguageSwitch.vue'
import ProfileAvatar from '../components/ProfileAvatar.vue'
import { pushToast } from '../toast'

const DEFAULT_LOCK_SECONDS = 30

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const next = computed(() => safeNext(route.query.next))
const loginLink = computed(() => ({ path: LOGIN_PATH, query: next.value !== '/' ? { next: next.value } : {} }))

const firstAccount = computed(() => !authStatus.has_users)
const adminCreates = computed(() => !!me.value?.is_admin)
const closed = computed(() => !authStatus.registration_open && !firstAccount.value && !adminCreates.value)

const form = reactive({
  name: '',
  password: '',
  confirm: '',
  color: AVATAR_COLORS[Math.floor(Math.random() * AVATAR_COLORS.length)]!,
})
const busy = ref(false)
const error = ref<string | null>(null)
const lockedUntil = ref(0)
const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
const secondsLeft = computed(() => Math.max(0, Math.ceil((lockedUntil.value - now.value) / 1000)))

const tooShort = computed(() => !!form.password && form.password.length < MIN_PASSWORD_LENGTH)
const mismatch = computed(() => !!form.confirm && form.password !== form.confirm)
const canSubmit = computed(
  () =>
    !!form.name.trim() &&
    form.password.length >= MIN_PASSWORD_LENGTH &&
    form.password === form.confirm &&
    !busy.value &&
    secondsLeft.value === 0,
)

function errorText(e: unknown): string {
  for (const code of ['name_taken', 'invalid_password', 'invalid_name', 'registration_closed']) {
    if (isApiError(e, code)) return t(`auth.register.errors.${code}`, { n: MIN_PASSWORD_LENGTH })
  }
  if (isApiError(e, 'wrong_auth_mode')) return t('auth.wrongMode')
  return e instanceof Error ? e.message : String(e)
}

async function submit() {
  error.value = null
  const name = form.name.trim()
  if (!name) {
    error.value = t('profile.nameRequired')
    return
  }
  if (form.password.length < MIN_PASSWORD_LENGTH) {
    error.value = t('auth.passwordTooShort', { n: MIN_PASSWORD_LENGTH })
    return
  }
  if (form.password !== form.confirm) {
    error.value = t('profile.passwordMismatch')
    return
  }
  busy.value = true
  const body = { name, password: form.password, avatar_color: form.color }
  try {
    if (adminCreates.value) {
      await api.createUser(body)
      pushToast(t('family.memberCreated', { name }))
      await router.push({ path: '/beallitasok', hash: '#profil' })
      return
    }
    await register(body, next.value)
  } catch (e) {
    if (isApiError(e, 'too_many_attempts') || (isApiError(e) && e.status === 429)) {
      lockedUntil.value = Date.now() + (e.retryAfter ?? DEFAULT_LOCK_SECONDS) * 1000
    } else {
      error.value = errorText(e)
    }
    busy.value = false
  }
}

onMounted(() => {
  if (!accountsMode.value) {
    void router.replace({ path: PROFILE_PATH, query: route.query })
    return
  }
  ticker = setInterval(() => (now.value = Date.now()), 1000)
  document.getElementById('reg-name')?.focus()
})
onBeforeUnmount(() => clearInterval(ticker))
</script>

<template>
  <div class="auth-page">
    <div class="top">
      <span class="brand">{{ t('app.name') }}</span>
      <LanguageSwitch compact />
    </div>

    <div v-if="closed" class="card auth-card">
      <h1>{{ t('auth.register.title') }}</h1>
      <p class="small">{{ t('auth.register.closed') }}</p>
      <RouterLink class="cta" :to="loginLink">{{ t('auth.register.toLogin') }}</RouterLink>
    </div>

    <form v-else class="card auth-card" @submit.prevent="submit">
      <h1>{{ adminCreates ? t('family.newMember') : t('auth.register.title') }}</h1>
      <p v-if="firstAccount" class="small note strong">{{ t('auth.register.firstAdmin') }}</p>
      <p v-else-if="adminCreates" class="small note">{{ t('auth.register.adminCreates') }}</p>

      <label for="reg-name" class="small">{{ t('auth.name') }}</label>
      <input id="reg-name" v-model="form.name" type="text" maxlength="40" autocomplete="username" :disabled="busy" />

      <label for="reg-pw" class="small">{{ t('auth.password') }}</label>
      <input id="reg-pw" v-model="form.password" type="password" autocomplete="new-password" :disabled="busy" />
      <p class="small" :class="tooShort ? 'warn' : 'muted'">{{ t('auth.passwordTooShort', { n: MIN_PASSWORD_LENGTH }) }}</p>

      <label for="reg-pw2" class="small">{{ t('profile.passwordConfirm') }}</label>
      <input id="reg-pw2" v-model="form.confirm" type="password" autocomplete="new-password" :disabled="busy" />
      <p v-if="mismatch" class="small warn">{{ t('profile.passwordMismatch') }}</p>

      <span class="small">{{ t('profile.color') }}</span>
      <div class="preview">
        <ProfileAvatar :name="form.name || '?'" :color="form.color" :size="36" />
        <ColorPalette v-model="form.color" :label="t('profile.color')" />
      </div>

      <p v-if="secondsLeft > 0" class="small warn" role="status">{{ t('auth.tooMany', { n: secondsLeft }) }}</p>
      <p v-else-if="error" class="small error" role="alert">{{ error }}</p>
      <button type="submit" class="primary" :disabled="!canSubmit">
        <span v-if="busy" class="spinner" aria-hidden="true"></span>
        {{ adminCreates ? t('family.create') : t('auth.register.submit') }}
      </button>
      <p v-if="!adminCreates && !firstAccount" class="small alt">
        {{ t('auth.register.haveAccount') }}
        <RouterLink :to="loginLink">{{ t('auth.register.loginLink') }}</RouterLink>
      </p>
    </form>
  </div>
</template>

<style scoped>
.auth-page {
  max-width: 420px;
  margin: 0 auto;
  padding-top: 8px;
}
.top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}
.brand {
  font-weight: 700;
  font-size: 1.1rem;
}
.auth-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.auth-card h1 {
  font-size: 1.3rem;
  margin: 0 0 8px;
  text-align: center;
}
.auth-card p {
  margin: 0;
}
.auth-card > button,
.auth-card > .cta {
  margin-top: 8px;
  align-self: stretch;
  justify-content: center;
  text-align: center;
}
input[type='password'],
input[type='text'] {
  width: 100%;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}
.preview {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.cta {
  display: block;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: var(--accent);
  color: var(--accent-text);
  font-weight: 600;
  text-decoration: none;
}
.cta:hover {
  filter: brightness(1.08);
}
.alt {
  margin-top: 8px !important;
  text-align: center;
}
.note {
  padding: 6px 10px;
  border-left: 3px solid var(--accent);
  background: var(--surface-2);
  border-radius: var(--radius);
}
.note.strong {
  font-weight: 600;
}
.error {
  color: var(--danger);
}
.warn {
  color: var(--warn);
}
</style>
