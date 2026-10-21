// Shared, module-level state for the DB-backed app settings and subscriptions (docs/api.md, M7).
import { computed, reactive } from 'vue'
import { api, type AppSettings, type AppSettingsPatch, type SubscriptionItem } from './api/client'
import { applyLang, currentLang, isUiLang, t, type UiLang } from './i18n'
import { PROVIDERS } from './providers'
import { pushError, pushToast } from './toast'

export const appSettings = reactive<{ loaded: boolean; data: AppSettings | null }>({ loaded: false, data: null })

export const subscriptions = reactive<{ loaded: boolean; items: SubscriptionItem[]; saving: boolean }>({
  loaded: false,
  items: [],
  saving: false,
})

/** Window event fired after a database restore; listeners re-read their server state. */
export const DATA_RESTORED_EVENT = 'wtw:data-restored'

/** Page-session UI flags (not persisted). */
export const ui = reactive({ onboardingDismissed: false })

export const enabledCount = computed(() => subscriptions.items.filter((s) => s.enabled).length)

/** Load once at start-up: the server's ui_language wins over localStorage / browser. */
export async function loadAppSettings(): Promise<void> {
  try {
    const s = await api.getAppSettings(true)
    appSettings.data = s
    if (isUiLang(s.ui_language) && s.ui_language !== currentLang()) applyLang(s.ui_language)
  } catch {
    // backend down or endpoint missing: keep the local language choice
  } finally {
    appSettings.loaded = true
  }
}

export async function updateAppSettings(patch: AppSettingsPatch, opts: { toast?: boolean } = {}): Promise<boolean> {
  const prev = appSettings.data ? { ...appSettings.data } : null
  if (appSettings.data) Object.assign(appSettings.data, patch)
  try {
    const res = await api.putAppSettings(patch)
    if (res && typeof res === 'object' && 'ui_language' in res) appSettings.data = res as AppSettings
    if (opts.toast) pushToast(t('settings.saved'))
    return true
  } catch {
    if (prev) appSettings.data = prev
    return false
  }
}

/** Language switch: applies immediately, persists to localStorage and (best effort) to the server. */
export async function setLanguage(lang: UiLang): Promise<void> {
  applyLang(lang)
  if (appSettings.data) appSettings.data.ui_language = lang
  try {
    await api.putAppSettings({ ui_language: lang }, true)
  } catch {
    // stays in localStorage; the server copy is updated next time
  }
}

export async function loadSubscriptions(force = false): Promise<void> {
  if (subscriptions.loaded && !force) return
  try {
    const res = await api.getSubscriptions()
    subscriptions.items = sortByProviders(res.items)
    subscriptions.loaded = true
  } catch {
    // toast already shown
  }
}

function sortByProviders(items: SubscriptionItem[]): SubscriptionItem[] {
  const order = PROVIDERS.map((p) => p.key)
  const idx = (k: string) => (order.includes(k) ? order.indexOf(k) : order.length)
  return [...items].sort((a, b) => idx(a.key) - idx(b.key))
}

let saveSeq = 0

/**
 * Optimistic per-provider update (enable flag and/or selected profile) via
 * PUT /settings/subscriptions {items: [...]}; a failure rolls the row back.
 */
export async function updateSubscription(
  key: string,
  patch: { enabled?: boolean; selected_profile?: string | null },
): Promise<boolean> {
  const item = subscriptions.items.find((s) => s.key === key)
  if (!item) return false
  const prev = { enabled: item.enabled, selected_profile: item.selected_profile }
  Object.assign(item, patch)
  const seq = ++saveSeq
  subscriptions.saving = true
  try {
    const res = await api.putSubscriptions([{ key, ...patch }])
    if (seq === saveSeq && res && typeof res === 'object' && Array.isArray((res as { items?: unknown }).items)) {
      subscriptions.items = sortByProviders((res as { items: SubscriptionItem[] }).items)
    }
    // The Netflix profile is the same thing as the app setting netflix_profile_name (backwards compatible).
    if (key === 'netflix' && 'selected_profile' in patch && appSettings.data) {
      appSettings.data.netflix_profile_name = patch.selected_profile ?? null
    }
    return true
  } catch {
    const cur = subscriptions.items.find((s) => s.key === key)
    if (cur) Object.assign(cur, prev)
    pushError(t('subscriptions.saveFailed'))
    return false
  } finally {
    if (seq === saveSeq) subscriptions.saving = false
  }
}

export function setSubscription(key: string, enabled: boolean): Promise<boolean> {
  return updateSubscription(key, { enabled })
}
