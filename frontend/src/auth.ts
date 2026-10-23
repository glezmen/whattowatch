// Current profile (M8/A, docs/api.md "Profilok és munkamenet"): session cookie set by the backend.
// Two auth modes (GET /auth/status): household = profile picker (/profil) with optional
// passwords; accounts = sign-in (/belepes) and self-registration (/regisztracio).
import { computed, reactive, ref } from 'vue'
import type { Router } from 'vue-router'
import { api, ApiError, onNoProfile, type AuthStatus, type Me } from './api/client'

export const PROFILE_PATH = '/profil'
export const LOGIN_PATH = '/belepes'
export const REGISTER_PATH = '/regisztracio'
/** Routes reachable without a session. */
const PUBLIC_PATHS = [PROFILE_PATH, LOGIN_PATH, REGISTER_PATH]
/** Minimum password length in accounts mode (the backend enforces the same). */
export const MIN_PASSWORD_LENGTH = 8

/** The current profile; `null` until known or when there is no valid session. */
export const me = ref<Me | null>(null)
/** True once `/auth/me` has answered (with a profile or 401). */
export const meKnown = ref(false)

/** GET /auth/status, loaded once at start-up; household until known (or if the call fails). */
export const authStatus = reactive<AuthStatus & { loaded: boolean }>({
  loaded: false,
  mode: 'household',
  registration_open: false,
  has_users: true,
})
export const accountsMode = computed(() => authStatus.mode === 'accounts')
/** Where a visitor without a session is sent: the picker or the sign-in page. */
export const entryPath = computed(() => (accountsMode.value ? LOGIN_PATH : PROFILE_PATH))

/** Fixed avatar palette (dark enough for white initials in both themes). */
export const AVATAR_COLORS = ['#2f6fed', '#c93636', '#2b8a3e', '#b26a00', '#8e44ad', '#00838f', '#d81b60', '#5d6370']

export async function loadMe(): Promise<Me | null> {
  try {
    me.value = await api.getMe()
  } catch {
    // 401 no_profile (or backend down): the picker / sign-in page shows what is wrong
    me.value = null
  } finally {
    meKnown.value = true
  }
  return me.value
}

export async function loadAuthStatus(): Promise<void> {
  try {
    const s = await api.getAuthStatus()
    authStatus.mode = s.mode === 'accounts' ? 'accounts' : 'household'
    authStatus.registration_open = !!s.registration_open
    authStatus.has_users = s.has_users !== false
  } catch {
    // older backend or backend down: behave as household mode
  } finally {
    authStatus.loaded = true
  }
}

/** Only same-origin app paths are accepted as `?next=` (never the auth pages themselves). */
export function safeNext(next: unknown): string {
  return typeof next === 'string' &&
    next.startsWith('/') &&
    !next.startsWith('//') &&
    !PUBLIC_PATHS.some((p) => next.startsWith(p))
    ? next
    : '/'
}

/** Full reload so every view, the bell and the settings re-read the new profile's data. */
function reloadAt(path: string): void {
  window.location.assign(path)
}

/** POST /auth/select, then reload at `next`. Throws ApiError (wrong_password, too_many_attempts). */
export async function selectProfile(id: number, password?: string | null, next = '/'): Promise<void> {
  await api.selectProfile(id, password)
  reloadAt(safeNext(next))
}

/** POST /auth/login, then reload at `next`. Throws ApiError (invalid_credentials, too_many_attempts). */
export async function login(name: string, password: string, next = '/'): Promise<void> {
  await api.login(name, password)
  reloadAt(safeNext(next))
}

/** POST /auth/register (sets the cookie), then reload at `next`. Throws ApiError. */
export async function register(
  body: { name: string; password: string; avatar_color?: string | null },
  next = '/',
): Promise<void> {
  await api.register(body)
  reloadAt(safeNext(next))
}

export async function logout(): Promise<void> {
  try {
    await api.logout()
  } catch {
    // the entry page is shown anyway; signing in again overwrites the cookie
  }
  me.value = null
  reloadAt(entryPath.value)
}

export function isApiError(e: unknown, code?: string): e is ApiError {
  return e instanceof ApiError && (code === undefined || e.code === code)
}

function isPublic(path: string): boolean {
  return PUBLIC_PATHS.includes(path)
}

/** Router guard + the client's `401 no_profile` hook. */
export function installAuth(router: Router): void {
  let redirecting = false
  onNoProfile(() => {
    me.value = null
    meKnown.value = true
    const cur = router.currentRoute.value
    // Before the first navigation has finished the guard redirects (and keeps ?next=).
    if (redirecting || !cur.matched.length || isPublic(cur.path)) return
    redirecting = true
    void router
      .replace({ path: entryPath.value, query: cur.fullPath !== '/' ? { next: cur.fullPath } : {} })
      .finally(() => {
        redirecting = false
      })
  })

  router.beforeEach(async (to) => {
    if (!meKnown.value || !authStatus.loaded) {
      await Promise.all([meKnown.value ? null : loadMe(), authStatus.loaded ? null : loadAuthStatus()])
    }
    // The pages of the other mode lead to this mode's entry page (keeping ?next=).
    if (accountsMode.value && to.path === PROFILE_PATH) return { path: LOGIN_PATH, query: to.query }
    if (!accountsMode.value && (to.path === LOGIN_PATH || to.path === REGISTER_PATH)) {
      return { path: PROFILE_PATH, query: to.query }
    }
    if (!me.value && !isPublic(to.path)) {
      return { path: entryPath.value, query: to.fullPath !== '/' ? { next: to.fullPath } : {} }
    }
    return true
  })
}
