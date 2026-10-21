// Current profile (M8/A, docs/api.md "Profilok és munkamenet"): session cookie set by the backend.
import { ref } from 'vue'
import type { Router } from 'vue-router'
import { api, ApiError, onNoProfile, type Me } from './api/client'

export const PROFILE_PATH = '/profil'

/** The current profile; `null` until known or when there is no valid session. */
export const me = ref<Me | null>(null)
/** True once `/auth/me` has answered (with a profile or 401). */
export const meKnown = ref(false)

/** Fixed avatar palette (dark enough for white initials in both themes). */
export const AVATAR_COLORS = ['#2f6fed', '#c93636', '#2b8a3e', '#b26a00', '#8e44ad', '#00838f', '#d81b60', '#5d6370']

export async function loadMe(): Promise<Me | null> {
  try {
    me.value = await api.getMe()
  } catch {
    // 401 no_profile (or backend down): the picker shows what is wrong
    me.value = null
  } finally {
    meKnown.value = true
  }
  return me.value
}

/** Only same-origin app paths are accepted as `?next=`. */
export function safeNext(next: unknown): string {
  return typeof next === 'string' && next.startsWith('/') && !next.startsWith('//') && !next.startsWith(PROFILE_PATH)
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

export async function logout(): Promise<void> {
  try {
    await api.logout()
  } catch {
    // the picker is shown anyway; selecting another profile overwrites the cookie
  }
  me.value = null
  reloadAt(PROFILE_PATH)
}

export function isApiError(e: unknown, code?: string): e is ApiError {
  return e instanceof ApiError && (code === undefined || e.code === code)
}

/** Router guard + the client's `401 no_profile` hook. */
export function installAuth(router: Router): void {
  let redirecting = false
  onNoProfile(() => {
    me.value = null
    meKnown.value = true
    const cur = router.currentRoute.value
    // Before the first navigation has finished the guard redirects (and keeps ?next=).
    if (redirecting || !cur.matched.length || cur.path === PROFILE_PATH) return
    redirecting = true
    void router
      .replace({ path: PROFILE_PATH, query: cur.fullPath !== '/' ? { next: cur.fullPath } : {} })
      .finally(() => {
        redirecting = false
      })
  })

  router.beforeEach(async (to) => {
    if (!meKnown.value) await loadMe()
    if (!me.value && to.path !== PROFILE_PATH) {
      return { path: PROFILE_PATH, query: to.fullPath !== '/' ? { next: to.fullPath } : {} }
    }
    return true
  })
}
