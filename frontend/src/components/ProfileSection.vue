<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type AppSettings, type AppSettingsPatch, type Me, type UserProfile } from '../api/client'
import { appSettings } from '../appSettings'
import {
  accountsMode,
  authStatus,
  AVATAR_COLORS,
  isApiError,
  loadAuthStatus,
  loadMe,
  logout,
  me,
  MIN_PASSWORD_LENGTH,
} from '../auth'
import { fmtDateTime } from '../format'
import { pushToast } from '../toast'
import ColorPalette from './ColorPalette.vue'
import ProfileAvatar from './ProfileAvatar.vue'
import ToggleSwitch from './ToggleSwitch.vue'

const CONFIRM_WORD = 'DELETE'
const { t } = useI18n()

function errorText(e: unknown): string {
  if (isApiError(e, 'wrong_password')) return t('profile.wrongPassword')
  if (isApiError(e, 'too_many_attempts') || (isApiError(e) && e.status === 429)) {
    return e.retryAfter ? t('auth.tooMany', { n: e.retryAfter }) : t('profile.tooManyShort')
  }
  if (isApiError(e, 'invalid_password')) return t('auth.passwordTooShort', { n: MIN_PASSWORD_LENGTH })
  if (isApiError(e, 'name_taken')) return t('auth.register.errors.name_taken')
  if (isApiError(e, 'invalid_name')) return t('auth.register.errors.invalid_name')
  if (isApiError(e, 'password_required')) return t('family.passwordRequired')
  return e instanceof Error ? e.message : String(e)
}

// --- name and colour ---
const form = reactive({ name: me.value?.name ?? '', color: me.value?.avatar_color || AVATAR_COLORS[0]! })
const savingBasics = ref(false)
const basicsError = ref<string | null>(null)
const basicsDirty = computed(
  () => !!me.value && (form.name.trim() !== me.value.name || form.color !== (me.value.avatar_color || AVATAR_COLORS[0])),
)

watch(
  () => me.value?.id,
  () => {
    form.name = me.value?.name ?? ''
    form.color = me.value?.avatar_color || AVATAR_COLORS[0]!
  },
)

async function saveBasics() {
  if (!me.value) return
  const name = form.name.trim()
  if (!name) {
    basicsError.value = t('profile.nameRequired')
    return
  }
  savingBasics.value = true
  basicsError.value = null
  try {
    const res = await api.updateMe({ name, avatar_color: form.color })
    if (res && typeof res === 'object' && 'id' in res) me.value = res as Me
    else me.value = { ...me.value, name, avatar_color: form.color }
    pushToast(t('settings.saved'))
  } catch (e) {
    basicsError.value = errorText(e)
  } finally {
    savingBasics.value = false
  }
}

// --- password ---
// Switch on = the profile has a password. Flipping it only opens the matching form; the
// change happens on submit (set: new + confirm; remove: current; change: current + new + confirm).
const pwSwitch = ref(!!me.value?.has_password)
const changing = ref(false)
const pw = reactive({ current: '', next: '', confirm: '' })
const pwBusy = ref(false)
const pwError = ref<string | null>(null)
const hasPassword = computed(() => !!me.value?.has_password)

type PwMode = 'set' | 'remove' | 'change' | null
const pwMode = computed<PwMode>(() => {
  // Accounts mode: the password is required, so it can only be set (if missing) or changed.
  if (accountsMode.value) return changing.value ? (hasPassword.value ? 'change' : 'set') : null
  if (!hasPassword.value && pwSwitch.value) return 'set'
  if (hasPassword.value && !pwSwitch.value) return 'remove'
  if (hasPassword.value && changing.value) return 'change'
  return null
})

function resetPw() {
  pw.current = ''
  pw.next = ''
  pw.confirm = ''
  pwError.value = null
}
watch(pwSwitch, () => {
  changing.value = false
  resetPw()
})
watch(hasPassword, (v) => (pwSwitch.value = v))

function cancelPw() {
  pwSwitch.value = hasPassword.value
  changing.value = false
  resetPw()
}

const minPwLength = computed(() => (accountsMode.value ? MIN_PASSWORD_LENGTH : 1))
const pwTooShort = computed(() => !!pw.next && pw.next.length < minPwLength.value)
const pwValid = computed(() => {
  if (pwMode.value === 'remove') return !!pw.current
  if (pwMode.value === 'set' || pwMode.value === 'change') {
    return (
      pw.next.length >= minPwLength.value && pw.next === pw.confirm && (pwMode.value === 'set' || !!pw.current)
    )
  }
  return false
})

async function submitPw() {
  if (!me.value || !pwMode.value) return
  if ((pwMode.value === 'set' || pwMode.value === 'change') && pw.next !== pw.confirm) {
    pwError.value = t('profile.passwordMismatch')
    return
  }
  if (pwMode.value !== 'remove' && pwTooShort.value) {
    pwError.value = t('auth.passwordTooShort', { n: MIN_PASSWORD_LENGTH })
    return
  }
  pwBusy.value = true
  pwError.value = null
  try {
    const removing = pwMode.value === 'remove'
    await api.setPassword({
      current_password: hasPassword.value ? pw.current : undefined,
      new_password: removing ? null : pw.next,
    })
    me.value = { ...me.value, has_password: !removing }
    changing.value = false
    resetPw()
    pushToast(
      removing
        ? t('profile.passwordRemoved')
        : accountsMode.value
          ? t('family.passwordSavedOtherSessions')
          : t('profile.passwordSaved'),
    )
  } catch (e) {
    pwError.value = errorText(e)
  } finally {
    pwBusy.value = false
  }
}

// --- profiles (admin) and deletion ---
const users = ref<UserProfile[]>([])
const others = computed(() => users.value.filter((u) => u.id !== me.value?.id))
const rowBusy = ref<number | null>(null)
const listError = ref<string | null>(null)

async function loadUsers() {
  try {
    users.value = (await api.listUsers()).items
  } catch (e) {
    listError.value = errorText(e)
  }
}

async function resetPassword(u: UserProfile) {
  rowBusy.value = u.id
  listError.value = null
  try {
    await api.resetUserPassword(u.id)
    pushToast(t('profile.resetDone', { name: u.name }))
    await loadUsers()
  } catch (e) {
    listError.value = errorText(e)
  } finally {
    rowBusy.value = null
  }
}

// --- family (accounts mode, admin): temporary password, new member ---
const tempFor = ref<number | null>(null)
const tempPw = ref('')
const tempError = ref<string | null>(null)

function openTemp(u: UserProfile) {
  tempFor.value = tempFor.value === u.id ? null : u.id
  tempPw.value = ''
  tempError.value = null
}

async function saveTemp(u: UserProfile) {
  if (tempPw.value.length < MIN_PASSWORD_LENGTH) {
    tempError.value = t('auth.passwordTooShort', { n: MIN_PASSWORD_LENGTH })
    return
  }
  rowBusy.value = u.id
  tempError.value = null
  try {
    await api.setUserPassword(u.id, tempPw.value)
    pushToast(t('family.tempPasswordSet', { name: u.name }))
    tempFor.value = null
    tempPw.value = ''
    await loadUsers()
  } catch (e) {
    tempError.value = errorText(e)
  } finally {
    rowBusy.value = null
  }
}

const member = reactive({ name: '', password: '' })
const memberBusy = ref(false)
const memberError = ref<string | null>(null)
const memberValid = computed(() => !!member.name.trim() && member.password.length >= MIN_PASSWORD_LENGTH)

async function createMember() {
  const name = member.name.trim()
  if (!name) {
    memberError.value = t('profile.nameRequired')
    return
  }
  if (member.password.length < MIN_PASSWORD_LENGTH) {
    memberError.value = t('auth.passwordTooShort', { n: MIN_PASSWORD_LENGTH })
    return
  }
  memberBusy.value = true
  memberError.value = null
  try {
    await api.createUser({
      name,
      password: member.password,
      avatar_color: AVATAR_COLORS[users.value.length % AVATAR_COLORS.length]!,
    })
    pushToast(t('family.memberCreated', { name }))
    member.name = ''
    member.password = ''
    await loadUsers()
  } catch (e) {
    memberError.value = errorText(e)
  } finally {
    memberBusy.value = false
  }
}

// --- global auth settings (admin): mode and registration ---
const authBusy = ref(false)
const authError = ref<string | null>(null)
const modeNeedsPassword = ref(false)

async function saveAuthSetting(patch: AppSettingsPatch): Promise<void> {
  authBusy.value = true
  authError.value = null
  modeNeedsPassword.value = false
  try {
    const res = await api.putAppSettings(patch, true)
    if (res && typeof res === 'object' && 'ui_language' in res) appSettings.data = res as AppSettings
    else if (appSettings.data) Object.assign(appSettings.data, patch)
    if (patch.auth_mode) authStatus.mode = patch.auth_mode
    if (patch.registration_open !== undefined) authStatus.registration_open = patch.registration_open
    await loadAuthStatus()
    pushToast(t('settings.saved'))
    if (patch.auth_mode === 'accounts') await loadUsers()
  } catch (e) {
    if (isApiError(e, 'password_required')) modeNeedsPassword.value = true
    else authError.value = errorText(e)
  } finally {
    authBusy.value = false
  }
}

const accountsSwitch = computed({
  get: () => accountsMode.value,
  set: (v: boolean) => void saveAuthSetting({ auth_mode: v ? 'accounts' : 'household' }),
})
const registrationSwitch = computed({
  get: () => appSettings.data?.registration_open ?? authStatus.registration_open,
  set: (v: boolean) => void saveAuthSetting({ registration_open: v }),
})

const dialog = ref<HTMLDialogElement | null>(null)
const target = ref<{ id: number; name: string; self: boolean } | null>(null)
const confirmText = ref('')
const deleting = ref(false)
const deleteError = ref<string | null>(null)

function askDelete(u: { id: number; name: string }) {
  target.value = { id: u.id, name: u.name, self: u.id === me.value?.id }
  confirmText.value = ''
  deleteError.value = null
  dialog.value?.showModal()
}
function closeDelete() {
  dialog.value?.close()
}

async function doDelete() {
  if (!target.value || confirmText.value !== CONFIRM_WORD) return
  deleting.value = true
  deleteError.value = null
  try {
    await api.deleteUser(target.value.id, confirmText.value)
    if (target.value.self) {
      // own profile gone: back to the picker (the session is no longer valid)
      await logout()
      return
    }
    pushToast(t('profile.deleted', { name: target.value.name }))
    closeDelete()
    await loadUsers()
  } catch (e) {
    deleteError.value = errorText(e)
  } finally {
    deleting.value = false
  }
}

onMounted(async () => {
  if (!me.value) await loadMe()
  if (me.value?.is_admin) await loadUsers()
})
</script>

<template>
  <div v-if="me" class="ps">
    <form class="block" @submit.prevent="saveBasics">
      <div class="who">
        <ProfileAvatar :name="form.name || me.name" :color="form.color" :size="48" />
        <span v-if="me.is_admin" class="tag">{{ t('profile.admin') }}</span>
      </div>
      <label for="ps-name" class="small">{{ t('profile.name') }}</label>
      <input id="ps-name" v-model="form.name" type="text" maxlength="40" autocomplete="off" />
      <span class="small">{{ t('profile.color') }}</span>
      <ColorPalette v-model="form.color" :label="t('profile.color')" />
      <p v-if="basicsError" class="small error" role="alert">{{ basicsError }}</p>
      <div>
        <button type="submit" class="primary" :disabled="savingBasics || !basicsDirty">
          <span v-if="savingBasics" class="spinner" aria-hidden="true"></span>
          {{ t('common.save') }}
        </button>
      </div>
    </form>

    <div class="block">
      <h3>{{ t('profile.passwordTitle') }}</h3>
      <template v-if="accountsMode">
        <p v-if="!hasPassword" class="small warn">{{ t('family.ownPasswordMissing') }}</p>
        <button v-if="!changing" type="button" class="link small" @click="changing = true">
          {{ hasPassword ? t('profile.changePassword') : t('family.setPassword') }}
        </button>
      </template>
      <ToggleSwitch
        v-else
        v-model="pwSwitch"
        :label="t('profile.passwordSwitch')"
        :hint="t('profile.passwordSwitchHint')"
      />
      <button
        v-if="!accountsMode && hasPassword && pwSwitch && !changing"
        type="button"
        class="link small"
        @click="changing = true"
      >
        {{ t('profile.changePassword') }}
      </button>
      <form v-if="pwMode" class="pwform" @submit.prevent="submitPw">
        <p v-if="pwMode === 'remove'" class="small muted">{{ t('profile.removeHint') }}</p>
        <template v-if="pwMode === 'remove' || pwMode === 'change'">
          <label for="ps-cur" class="small">{{ t('profile.currentPassword') }}</label>
          <input id="ps-cur" v-model="pw.current" type="password" autocomplete="current-password" />
        </template>
        <template v-if="pwMode === 'set' || pwMode === 'change'">
          <label for="ps-new" class="small">{{ t('profile.newPassword') }}</label>
          <input id="ps-new" v-model="pw.next" type="password" autocomplete="new-password" />
          <label for="ps-new2" class="small">{{ t('profile.passwordConfirm') }}</label>
          <input id="ps-new2" v-model="pw.confirm" type="password" autocomplete="new-password" />
          <p v-if="pw.confirm && pw.next !== pw.confirm" class="small warn">{{ t('profile.passwordMismatch') }}</p>
          <p v-if="accountsMode" class="small" :class="pwTooShort ? 'warn' : 'muted'">
            {{ t('auth.passwordTooShort', { n: MIN_PASSWORD_LENGTH }) }}
          </p>
        </template>
        <p v-if="pwError" class="small error" role="alert">{{ pwError }}</p>
        <div class="row">
          <button type="button" :disabled="pwBusy" @click="cancelPw">{{ t('profile.cancel') }}</button>
          <button type="submit" :class="pwMode === 'remove' ? 'danger-btn' : 'primary'" :disabled="pwBusy || !pwValid">
            <span v-if="pwBusy" class="spinner" aria-hidden="true"></span>
            {{ pwMode === 'remove' ? t('profile.removePassword') : t('profile.savePassword') }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="me.is_admin && accountsMode" class="block">
      <h3>{{ t('family.title') }}</h3>
      <ul class="users">
        <li v-for="u in users" :key="u.id">
          <div class="urow">
            <ProfileAvatar :name="u.name" :color="u.avatar_color" :size="28" />
            <span class="ucol">
              <span class="uname">
                {{ u.name }}
                <span v-if="u.id === me.id" class="small muted">{{ t('family.you') }}</span>
              </span>
              <span class="small muted">
                {{ u.last_active_at ? t('family.lastActive', { when: fmtDateTime(u.last_active_at) }) : t('family.neverActive') }}
              </span>
            </span>
            <span v-if="u.is_admin" class="tag">{{ t('profile.admin') }}</span>
            <span v-if="!u.has_password" class="small warn">{{ t('family.noPassword') }}</span>
            <span v-if="u.id !== me.id" class="actions">
              <button
                type="button"
                class="small"
                :aria-expanded="tempFor === u.id"
                :disabled="rowBusy === u.id"
                @click="openTemp(u)"
              >
                {{ t('family.tempPassword') }}
              </button>
              <button type="button" class="small danger-btn" :disabled="rowBusy === u.id" @click="askDelete(u)">
                {{ t('common.delete') }}
              </button>
            </span>
          </div>
          <form v-if="tempFor === u.id" class="tempform" @submit.prevent="saveTemp(u)">
            <label :for="`ps-temp-${u.id}`" class="small">{{ t('family.tempPasswordFor', { name: u.name }) }}</label>
            <input :id="`ps-temp-${u.id}`" v-model="tempPw" type="text" autocomplete="off" spellcheck="false" />
            <p class="small muted">{{ t('family.tempPasswordHint', { n: MIN_PASSWORD_LENGTH }) }}</p>
            <p v-if="tempError" class="small error" role="alert">{{ tempError }}</p>
            <div class="row">
              <button type="button" :disabled="rowBusy === u.id" @click="tempFor = null">{{ t('profile.cancel') }}</button>
              <button type="submit" class="primary" :disabled="rowBusy === u.id || tempPw.length < MIN_PASSWORD_LENGTH">
                <span v-if="rowBusy === u.id" class="spinner" aria-hidden="true"></span>
                {{ t('family.setTemp') }}
              </button>
            </div>
          </form>
        </li>
      </ul>
      <p v-if="listError" class="small error" role="alert">{{ listError }}</p>

      <form class="pwform" @submit.prevent="createMember">
        <h4>{{ t('family.newMember') }}</h4>
        <label for="ps-mem-name" class="small">{{ t('profile.name') }}</label>
        <input id="ps-mem-name" v-model="member.name" type="text" maxlength="40" autocomplete="off" />
        <label for="ps-mem-pw" class="small">{{ t('family.tempPassword') }}</label>
        <input id="ps-mem-pw" v-model="member.password" type="text" autocomplete="off" spellcheck="false" />
        <p class="small muted">{{ t('family.tempPasswordHint', { n: MIN_PASSWORD_LENGTH }) }}</p>
        <p v-if="memberError" class="small error" role="alert">{{ memberError }}</p>
        <div class="row">
          <button type="submit" class="primary" :disabled="memberBusy || !memberValid">
            <span v-if="memberBusy" class="spinner" aria-hidden="true"></span>
            {{ t('family.create') }}
          </button>
        </div>
      </form>

      <ToggleSwitch
        v-model="registrationSwitch"
        :disabled="authBusy"
        :label="t('family.registrationOpen')"
        :hint="t('family.registrationOpenHint')"
      />
    </div>

    <div v-if="me.is_admin && !accountsMode" class="block">
      <h3>{{ t('profile.profilesTitle') }}</h3>
      <p v-if="!others.length" class="small muted">{{ t('profile.noOthers') }}</p>
      <ul v-else class="users">
        <li v-for="u in others" :key="u.id">
          <ProfileAvatar :name="u.name" :color="u.avatar_color" :size="28" />
          <span class="uname">{{ u.name }}</span>
          <span v-if="u.is_admin" class="tag">{{ t('profile.admin') }}</span>
          <span v-if="u.has_password" class="small muted">{{ t('profile.hasPassword') }}</span>
          <span class="actions">
            <button
              v-if="u.has_password"
              type="button"
              class="small"
              :disabled="rowBusy === u.id"
              @click="resetPassword(u)"
            >
              {{ t('profile.resetPassword') }}
            </button>
            <button type="button" class="small danger-btn" :disabled="rowBusy === u.id" @click="askDelete(u)">
              {{ t('common.delete') }}
            </button>
          </span>
        </li>
      </ul>
      <p v-if="listError" class="small error" role="alert">{{ listError }}</p>
    </div>
    <p v-if="!me.is_admin" class="small note">{{ t('profile.adminOnlyNote') }}</p>

    <div v-if="me.is_admin" class="block">
      <h3>{{ t('family.modeTitle') }}</h3>
      <ToggleSwitch
        v-model="accountsSwitch"
        :disabled="authBusy"
        :label="t('family.accountsMode')"
        :hint="t('family.accountsModeHint')"
      />
      <p v-if="modeNeedsPassword" class="small warn" role="alert">{{ t('family.modeNeedsPassword') }}</p>
      <p v-if="authError" class="small error" role="alert">{{ authError }}</p>
    </div>

    <div class="block">
      <h3>{{ t('profile.deleteTitle') }}</h3>
      <p class="small muted">{{ t('profile.deleteHint') }}</p>
      <div>
        <button type="button" class="danger-btn" @click="askDelete(me)">{{ t('profile.deleteOwn') }}</button>
      </div>
    </div>

    <dialog ref="dialog" class="confirm" aria-labelledby="ps-del-title" @cancel="deleting && $event.preventDefault()">
      <form method="dialog" @submit.prevent="doDelete">
        <h3 id="ps-del-title">{{ target?.self ? t('profile.deleteOwnTitle') : t('profile.deleteOtherTitle', { name: target?.name ?? '' }) }}</h3>
        <p class="small">{{ t('profile.deleteBody') }}</p>
        <label for="ps-del-confirm" class="small">{{ t('profile.confirmLabel', { word: CONFIRM_WORD }) }}</label>
        <input
          id="ps-del-confirm"
          v-model="confirmText"
          type="text"
          autocomplete="off"
          spellcheck="false"
          :placeholder="CONFIRM_WORD"
        />
        <p v-if="deleteError" class="small error" role="alert">{{ deleteError }}</p>
        <div class="row">
          <button type="button" :disabled="deleting" @click="closeDelete">{{ t('profile.cancel') }}</button>
          <button type="submit" class="danger-btn solid" :disabled="deleting || confirmText !== CONFIRM_WORD">
            <span v-if="deleting" class="spinner" aria-hidden="true"></span>
            {{ t('common.delete') }}
          </button>
        </div>
      </form>
    </dialog>
  </div>
</template>

<style scoped>
.ps {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.block {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  max-width: 520px;
}
.block p {
  margin: 0;
}
h3 {
  font-size: 1rem;
  margin: 0 0 2px;
}
.who {
  display: flex;
  align-items: center;
  gap: 10px;
}
input[type='text'],
input[type='password'] {
  width: 100%;
  max-width: 360px;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}
.pwform {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  margin-top: 4px;
}
.pwform p {
  margin: 0;
}
.row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
}
.users {
  list-style: none;
  margin: 0;
  padding: 0;
  width: 100%;
}
.users li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.urow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  width: 100%;
}
.ucol {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.tempform {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding-left: 38px;
}
.tempform p {
  margin: 0;
}
h4 {
  font-size: 0.95rem;
  margin: 8px 0 0;
}
.users li:last-child {
  border-bottom: none;
}
.uname {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.actions {
  display: flex;
  gap: 6px;
  margin-left: auto;
}
.note {
  padding: 6px 10px;
  border-left: 3px solid var(--accent);
  background: var(--surface-2);
  border-radius: var(--radius);
  max-width: 640px;
}
.error {
  color: var(--danger);
}
.warn {
  color: var(--warn);
}
.danger-btn {
  border-color: var(--danger);
  color: var(--danger);
  font-weight: 600;
}
.danger-btn.solid:not(:disabled),
.pwform .danger-btn:not(:disabled) {
  background: var(--danger);
  color: #fff;
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
  color: var(--danger);
}
.confirm input {
  display: block;
  width: 100%;
  max-width: none;
  margin-top: 4px;
}
.confirm .row {
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
