<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type MediaType, type RecommendationItem } from '../api/client'
import { appSettings, enabledCount, includeWatchlist, loadSubscriptions, subscriptions, ui } from '../appSettings'
import ChatPanel from '../components/ChatPanel.vue'
import GenreFilterPill from '../components/GenreFilterPill.vue'
import OnboardingPanel from '../components/OnboardingPanel.vue'
import TitleCard from '../components/TitleCard.vue'
import SortSelect from '../components/SortSelect.vue'
import WatchlistToggle from '../components/WatchlistToggle.vue'
import { fmtDateTime } from '../format'
import { activeGenre } from '../genreFilter'
import { useSortOrder } from '../sortOrder'

const { t } = useI18n()
const items = ref<RecommendationItem[]>([])
const loading = ref(false)
const refreshing = ref(false)
const loaded = ref(false)
const mediaType = ref<MediaType | ''>('')
const filteredWatchlist = ref(0)
const sort = useSortOrder('home')

watch(includeWatchlist, () => void load())

async function load(refresh = false) {
  if (refresh) refreshing.value = true
  else loading.value = true
  try {
    const p = {
      limit: 20, media_type: mediaType.value, include_watchlist: includeWatchlist.value, sort: sort.value,
      genre: activeGenre.value || undefined,
    }
    const res = refresh ? await api.refreshRecommendations(p) : await api.getRecommendations(p)
    items.value = res.items
    filteredWatchlist.value = res.filteredWatchlist
    loaded.value = true
  } catch {
    // toast already shown
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

watch(mediaType, () => load())
watch(sort, () => void load(false))
watch(activeGenre, () => void load(false))
onMounted(() => {
  void load()
  void loadSubscriptions()
})

// The onboarding panel already warns about missing subscriptions while it is visible.
const noSubs = computed(
  () => subscriptions.loaded && enabledCount.value === 0 && (ui.onboardingDismissed || !appSettings.data),
)

const types = computed<{ value: MediaType | ''; label: string }[]>(() => [
  { value: '', label: t('common.all') },
  { value: 'movie', label: t('mediaType.movieCap') },
  { value: 'tv', label: t('mediaType.tvCap') },
])
</script>

<template>
  <OnboardingPanel @done="load()" />
  <ChatPanel compact />

  <h1>{{ t('home.title') }}</h1>
  <div v-if="noSubs" class="banner small" role="status">
    {{ t('home.noSubscriptions') }} <RouterLink to="/beallitasok">{{ t('nav.settings') }}</RouterLink>
  </div>
  <div class="toolbar">
    <div class="segmented" role="group" :aria-label="t('common.type')">
      <button v-for="ty in types" :key="ty.value" type="button" :class="{ active: mediaType === ty.value }" @click="mediaType = ty.value">
        {{ ty.label }}
      </button>
    </div>
    <WatchlistToggle :hidden="filteredWatchlist" />
    <GenreFilterPill />
    <SortSelect v-model="sort" :default-label="t('sort.defaults.home')" />
    <button type="button" class="primary" :disabled="refreshing" @click="load(true)">
      <span v-if="refreshing" class="spinner" aria-hidden="true"></span>
      {{ t('home.refresh') }}
    </button>
    <span v-if="refreshing" class="small muted">{{ t('home.refreshing') }}</span>
    <span v-else-if="items.length" class="small muted">{{ t('home.generated', { date: fmtDateTime(items[0]!.generated_at) }) }}</span>
  </div>

  <p v-if="loading" class="empty"><span class="spinner"></span> {{ t('common.loading') }}</p>
  <p v-else-if="loaded && !items.length" class="empty">{{ t('home.empty') }}</p>
  <div v-else class="grid">
    <TitleCard
      v-for="it in items"
      :key="it.batch_id + it.title.id"
      :title="it.title"
      :reasons="it.reasons"
      :reason-codes="it.reason_codes"
      :batch-id="it.stored !== false ? it.batch_id : undefined"
    />
  </div>
</template>
