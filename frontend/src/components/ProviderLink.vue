<script lang="ts">
import { reactive } from 'vue'
import type { TitleLinks } from '../api/client'

/** Fetched links per title, for the page session. */
const linkCache = reactive(new Map<number, TitleLinks>())
</script>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api/client'
import { PROVIDER_BY_KEY } from '../providers'
import { pushToast } from '../toast'
import ProviderBadge from './ProviderBadge.vue'

const props = defineProps<{
  titleId: number
  providerKey: string
  fallbackName?: string
  deepLink?: string | null
}>()

const { t } = useI18n()
const loading = ref(false)
const name = computed(() => PROVIDER_BY_KEY[props.providerKey]?.name ?? props.fallbackName ?? props.providerKey)

function pick(links: TitleLinks): { url: string; direct: boolean } | null {
  const mine = links.items.filter((i) => i.provider_key === props.providerKey)
  const best = mine.find((i) => i.offer_type === 'flatrate') ?? mine[0]
  if (best) return { url: best.url, direct: true }
  const fb = links.fallback_search[props.providerKey]
  return fb ? { url: fb, direct: false } : null
}

const cached = computed(() => {
  const l = linkCache.get(props.titleId)
  return l ? pick(l) : null
})
const href = computed(() => props.deepLink || cached.value?.url || null)
const isDirect = computed(() => !!props.deepLink || cached.value?.direct === true)
const tip = computed(() => (isDirect.value ? t('providerLink.open', { name: name.value }) : t('providerLink.search')))

async function onClick() {
  if (loading.value) return
  const w = window.open('', '_blank')
  if (w) w.opener = null
  loading.value = true
  try {
    const links = await api.getTitleLinks(props.titleId)
    linkCache.set(props.titleId, links)
    const r = pick(links)
    if (r && w) w.location.href = r.url
    else if (r) window.open(r.url, '_blank', 'noopener,noreferrer')
    else {
      w?.close()
      pushToast(t('providerLink.none'))
    }
  } catch {
    w?.close()
    pushToast(t('providerLink.none'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <a v-if="href" class="plink" :href="href" target="_blank" rel="noopener noreferrer" :title="tip">
    <ProviderBadge :provider-key="providerKey" :fallback-name="fallbackName" />
    <svg class="ext" viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
      <path d="M9 2h5v5M14 2 7 9M12 9v4a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </a>
  <button v-else type="button" class="plink" :title="tip" :disabled="loading" @click="onClick">
    <ProviderBadge :provider-key="providerKey" :fallback-name="fallbackName" />
    <span v-if="loading" class="spin" aria-hidden="true"></span>
    <svg v-else class="ext" viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
      <path d="M9 2h5v5M14 2 7 9M12 9v4a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </button>
</template>

<style scoped>
.plink {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 0;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  text-decoration: none;
  font: inherit;
}
.plink:hover {
  color: var(--accent);
}
.plink:disabled {
  cursor: progress;
}
.spin {
  width: 10px;
  height: 10px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: plink-spin 0.7s linear infinite;
}
@keyframes plink-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
