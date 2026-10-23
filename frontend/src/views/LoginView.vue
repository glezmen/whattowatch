<script setup lang="ts">
// Accounts mode: sign in with name + password (POST /auth/login). Public route.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { accountsMode, authStatus, isApiError, login, PROFILE_PATH, REGISTER_PATH, safeNext } from '../auth'
import LanguageSwitch from '../components/LanguageSwitch.vue'

const DEFAULT_LOCK_SECONDS = 30

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const next = computed(() => safeNext(route.query.next))
const registerLink = computed(() => ({ path: REGISTER_PATH, query: next.value !== '/' ? { next: next.value } : {} }))

const name = ref('')
const password = ref('')
const busy = ref(false)
const error = ref<string | null>(null)
const lockedUntil = ref(0)
const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined

const secondsLeft = computed(() => Math.max(0, Math.ceil((lockedUntil.value - now.value) / 1000)))
const canSubmit = computed(() => !!name.value.trim() && !!password.value && !busy.value && secondsLeft.value === 0)

async function submit() {
  if (!canSubmit.value) return
  busy.value = true
  error.value = null
  try {
    await login(name.value.trim(), password.value, next.value)
  } catch (e) {
    if (isApiError(e, 'too_many_attempts') || (isApiError(e) && e.status === 429)) {
      lockedUntil.value = Date.now() + (e.retryAfter ?? DEFAULT_LOCK_SECONDS) * 1000
    } else if (isApiError(e, 'invalid_credentials') || (isApiError(e) && e.status === 401)) {
      error.value = t('auth.login.invalidCredentials')
    } else if (isApiError(e, 'wrong_auth_mode')) {
      error.value = t('auth.wrongMode')
    } else {
      error.value = e instanceof Error ? e.message : String(e)
    }
    password.value = ''
    busy.value = false
  }
}

onMounted(() => {
  if (!accountsMode.value) {
    void router.replace({ path: PROFILE_PATH, query: route.query })
    return
  }
  ticker = setInterval(() => (now.value = Date.now()), 1000)
  document.getElementById('login-name')?.focus()
})
onBeforeUnmount(() => clearInterval(ticker))
</script>

<template>
  <div class="auth-page">
    <div class="top">
      <span class="brand">{{ t('app.name') }}</span>
      <LanguageSwitch compact />
    </div>

    <div v-if="!authStatus.has_users" class="card auth-card">
      <h1>{{ t('auth.login.welcome') }}</h1>
      <p class="small note">{{ t('auth.login.firstNote') }}</p>
      <RouterLink class="cta" :to="registerLink">{{ t('auth.login.registerFirst') }}</RouterLink>
    </div>

    <form v-else class="card auth-card" @submit.prevent="submit">
      <h1>{{ t('auth.login.title') }}</h1>
      <label for="login-name" class="small">{{ t('auth.name') }}</label>
      <input id="login-name" v-model="name" type="text" maxlength="40" autocomplete="username" :disabled="busy" />
      <label for="login-pw" class="small">{{ t('auth.password') }}</label>
      <input id="login-pw" v-model="password" type="password" autocomplete="current-password" :disabled="busy" />
      <p v-if="secondsLeft > 0" class="small warn" role="status">{{ t('auth.tooMany', { n: secondsLeft }) }}</p>
      <p v-else-if="error" class="small error" role="alert">{{ error }}</p>
      <button type="submit" class="primary" :disabled="!canSubmit">
        <span v-if="busy" class="spinner" aria-hidden="true"></span>
        {{ t('auth.login.submit') }}
      </button>
      <p v-if="authStatus.registration_open" class="small alt">
        {{ t('auth.login.noAccount') }}
        <RouterLink :to="registerLink">{{ t('auth.login.registerLink') }}</RouterLink>
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
.error {
  color: var(--danger);
}
.warn {
  color: var(--warn);
}
</style>
