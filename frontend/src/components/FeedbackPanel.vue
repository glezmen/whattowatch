<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, type PreferenceSignal, type UserState } from '../api/client'
import { pushToast } from '../toast'
import StarRating from './StarRating.vue'

type TextField = 'liked_text' | 'disliked_text'

const props = defineProps<{ titleId: number; user: UserState; startOpen?: boolean }>()
const emit = defineEmits<{ 'update:user': [user: UserState] }>()

const busy = ref(false)
const saving = ref(false)
const open = ref(!!props.startOpen)
const drafts = ref<Record<TextField, string>>({
  liked_text: props.user.liked_text ?? '',
  disliked_text: props.user.disliked_text ?? '',
})
const signals = ref<PreferenceSignal[] | null>(null)

watch(
  () => [props.user.liked_text, props.user.disliked_text],
  ([l, d]) => {
    if (!saving.value) drafts.value = { liked_text: l ?? '', disliked_text: d ?? '' }
  },
)

const appWatchlist = computed(() => props.user.watchlist_sources.includes('app'))
const positive = computed(() => (signals.value ?? []).filter((s) => s.polarity === '+'))
const negative = computed(() => (signals.value ?? []).filter((s) => s.polarity === '-'))

function dirty(f: TextField): boolean {
  return drafts.value[f].trim() !== (props.user[f] ?? '').trim()
}

/**
 * Optimistic update: the patch is shown immediately, then replaced by the server's
 * UserState; on error only the patched keys are rolled back.
 */
async function optimistic(patch: Partial<UserState>, call: () => Promise<UserState>) {
  const prev = Object.fromEntries(Object.keys(patch).map((k) => [k, props.user[k as keyof UserState]]))
  emit('update:user', { ...props.user, ...patch })
  busy.value = true
  try {
    const server = await call()
    if (server && typeof server === 'object') emit('update:user', server)
  } catch {
    emit('update:user', { ...props.user, ...prev })
  } finally {
    busy.value = false
  }
}

function toggleSeen() {
  const seen = !props.user.seen
  optimistic({ seen, seen_source: seen ? 'manual' : null }, () => api.putSeen(props.titleId, seen))
}

function setStars(stars: number | null) {
  optimistic({ stars }, () => api.putRating(props.titleId, stars))
}

function toggleWatchlist() {
  if (appWatchlist.value) {
    const sources = props.user.watchlist_sources.filter((s) => s !== 'app')
    optimistic({ watchlist_sources: sources, on_watchlist: sources.length > 0 }, () =>
      api.removeFromWatchlist(props.titleId),
    )
  } else {
    optimistic({ watchlist_sources: [...props.user.watchlist_sources, 'app'], on_watchlist: true }, () =>
      api.addToWatchlist(props.titleId),
    )
  }
}

function toggleNotInterested() {
  const value = !props.user.not_interested
  optimistic({ not_interested: value }, () => api.putNotInterested(props.titleId, value))
}

async function loadSignals() {
  try {
    signals.value = await api.getSignals(props.titleId)
  } catch {
    signals.value = []
  }
}

watch(
  open,
  (o) => {
    if (o && signals.value === null && (props.user.liked_text || props.user.disliked_text)) loadSignals()
  },
  { immediate: true },
)

/** Sends only the given fields; a missing key means "unchanged" per the contract. */
async function saveFields(fields: Partial<Record<TextField, string | null>>) {
  if (Object.keys(fields).length === 0) return
  saving.value = true
  try {
    const res = await api.putFeedback(props.titleId, fields)
    emit('update:user', res.user)
    signals.value = res.signals
    pushToast('Visszajelzés elmentve.')
  } catch {
    // Drafts are kept so the text is not lost; the toast shows the error.
  } finally {
    saving.value = false
  }
}

function saveField(f: TextField) {
  if (!dirty(f)) return
  const v = drafts.value[f].trim()
  saveFields({ [f]: v === '' ? null : v })
}

function saveAll() {
  const body: Partial<Record<TextField, string | null>> = {}
  for (const f of ['liked_text', 'disliked_text'] as const) {
    if (dirty(f)) {
      const v = drafts.value[f].trim()
      body[f] = v === '' ? null : v
    }
  }
  saveFields(body)
}

function clearField(f: TextField) {
  drafts.value[f] = ''
  if (props.user[f]) saveFields({ [f]: null })
}

async function removeSignal(s: PreferenceSignal) {
  const before = signals.value
  signals.value = (signals.value ?? []).filter((x) => x.id !== s.id)
  try {
    await api.deleteSignal(s.id)
  } catch {
    signals.value = before
  }
}
</script>

<template>
  <div class="feedback">
    <div class="quick">
      <button type="button" class="toggle" :class="{ active: user.seen }" :aria-pressed="user.seen" :disabled="busy" @click="toggleSeen">
        {{ user.seen ? '✓ Már láttam' : 'Már láttam' }}
      </button>
      <button
        type="button"
        class="toggle"
        :class="{ active: appWatchlist }"
        :aria-pressed="appWatchlist"
        :disabled="busy"
        :title="appWatchlist ? 'Eltávolítás az app watchlistjéről' : 'Mentés az app watchlistjére'"
        @click="toggleWatchlist"
      >
        {{ appWatchlist ? '✓ Watchlisten' : '+ Watchlist' }}
      </button>
      <button
        type="button"
        class="toggle danger"
        :class="{ active: user.not_interested }"
        :aria-pressed="user.not_interested"
        :disabled="busy"
        @click="toggleNotInterested"
      >
        Nem érdekel
      </button>
    </div>
    <div class="row">
      <span class="small muted">Saját értékelés:</span>
      <StarRating :model-value="user.stars" :disabled="busy" @update:model-value="setStars" />
    </div>

    <button type="button" class="link small" :aria-expanded="open" @click="open = !open">
      {{ open ? '▾' : '▸' }} Szöveges vélemény{{ user.liked_text || user.disliked_text ? ' (van)' : '' }}
    </button>
    <div v-if="open" class="texts">
      <p class="small muted">A két mező külön kerül feldolgozásra; mindkettő opcionális.</p>
      <label class="field positive">
        <span class="label">Mi tetszett benne?</span>
        <span class="input-wrap">
          <textarea v-model="drafts.liked_text" rows="2" :disabled="saving" @blur="saveField('liked_text')" />
          <button
            v-if="drafts.liked_text || user.liked_text"
            type="button"
            class="icon-btn"
            aria-label="Pozitív vélemény törlése"
            title="Törlés"
            :disabled="saving"
            @mousedown.prevent
            @click="clearField('liked_text')"
          >×</button>
        </span>
      </label>
      <label class="field negative">
        <span class="label">Mi nem tetszett benne?</span>
        <span class="input-wrap">
          <textarea v-model="drafts.disliked_text" rows="2" :disabled="saving" @blur="saveField('disliked_text')" />
          <button
            v-if="drafts.disliked_text || user.disliked_text"
            type="button"
            class="icon-btn"
            aria-label="Negatív vélemény törlése"
            title="Törlés"
            :disabled="saving"
            @mousedown.prevent
            @click="clearField('disliked_text')"
          >×</button>
        </span>
      </label>
      <div class="row">
        <button type="button" :disabled="saving || (!dirty('liked_text') && !dirty('disliked_text'))" @click="saveAll">
          {{ saving ? 'Mentés…' : 'Mentés' }}
        </button>
      </div>
      <div v-if="signals && signals.length" class="signals">
        <span class="small muted">Kinyert jelzések (törölhetők):</span>
        <span v-for="s in [...positive, ...negative]" :key="s.id" class="chip" :class="s.polarity === '+' ? 'pos' : 'neg'" :title="`${s.category}${s.extractor ? ' · ' + s.extractor : ''}`">
          {{ s.polarity === '+' ? '+' : '−' }} {{ s.label_name || s.label }}
          <button type="button" class="icon-btn" :aria-label="`Jelzés törlése: ${s.label_name || s.label}`" @click="removeSignal(s)">×</button>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.feedback {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.quick {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.texts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.texts p {
  margin: 0;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-left: 8px;
  border-left: 3px solid var(--border);
}
.field.positive {
  border-left-color: var(--positive);
}
.field.negative {
  border-left-color: var(--danger);
}
.label {
  font-size: 0.85rem;
  font-weight: 600;
}
.input-wrap {
  display: flex;
  gap: 4px;
  align-items: flex-start;
}
.input-wrap textarea {
  flex: 1;
  min-width: 0;
}
.signals {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.chip.pos {
  border-color: var(--positive);
}
.chip.neg {
  border-color: var(--danger);
}
.chip .icon-btn {
  padding: 0 2px;
}
</style>
