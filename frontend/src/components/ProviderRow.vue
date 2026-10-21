<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { SubscriptionItem, SubscriptionProfile } from '../api/client'
import { updateSubscription } from '../appSettings'
import ProviderBadge from './ProviderBadge.vue'
import ToggleSwitch from './ToggleSwitch.vue'

const props = defineProps<{ item: SubscriptionItem; compact?: boolean }>()
const { t } = useI18n()

const logoFailed = ref(false)
watch(
  () => props.item.logo_url,
  () => (logoFailed.value = false),
)

const profiles = computed<SubscriptionProfile[]>(() => props.item.profiles ?? [])
const hasProfiles = computed(() => props.item.profile_support !== 'none' && profiles.value.length > 0)
const selected = computed(() => props.item.selected_profile ?? '')
const selectId = computed(() => `profile-${props.item.key}`)

function profileLabel(p: SubscriptionProfile): string {
  const extra: string[] = []
  if (p.primary_lang) extra.push(p.primary_lang)
  if (p.rows !== undefined && p.rows !== null) extra.push(t('providers.rows', { n: p.rows }, p.rows))
  return extra.length ? `${p.name} (${extra.join(', ')})` : p.name
}

function onToggle(enabled: boolean) {
  void updateSubscription(props.item.key, { enabled })
}

function onProfile(e: Event) {
  const v = (e.target as HTMLSelectElement).value
  void updateSubscription(props.item.key, { selected_profile: v || null })
}
</script>

<template>
  <li class="provider-row" :class="{ off: !item.enabled, compact }">
    <ToggleSwitch class="main" :model-value="item.enabled" @update:model-value="onToggle">
      <img
        v-if="item.logo_url && !logoFailed"
        class="logo"
        :src="item.logo_url"
        alt=""
        width="32"
        height="32"
        loading="lazy"
        @error="logoFailed = true"
      />
      <ProviderBadge v-else :provider-key="item.key" :fallback-name="item.display_name" />
      <span class="name">{{ item.display_name }}</span>
    </ToggleSwitch>
    <div v-if="!compact || hasProfiles" class="profile">
      <label :for="selectId" class="visually-hidden">{{ t('providers.profile', { name: item.display_name }) }}</label>
      <select v-if="hasProfiles" :id="selectId" :value="selected" @change="onProfile">
        <option value="">{{ t('providers.noProfile') }}</option>
        <option v-for="p in profiles" :key="p.name" :value="p.name">{{ profileLabel(p) }}</option>
        <option v-if="selected && !profiles.some((p) => p.name === selected)" :value="selected">{{ selected }}</option>
      </select>
      <template v-else>
        <select :id="selectId" disabled :title="t('providers.profileHint')">
          <option>{{ t('providers.noProfileData') }}</option>
        </select>
        <RouterLink v-if="!compact" to="/import" class="small hint" :title="t('providers.profileHint')">
          {{ t('providers.profileHint') }}
        </RouterLink>
      </template>
    </div>
  </li>
</template>

<style scoped>
.provider-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 6px 16px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.provider-row:last-child {
  border-bottom: none;
}
.provider-row.compact {
  padding: 4px 0;
}
.provider-row.off .main img,
.provider-row.off .main .name,
.provider-row.off .main :deep(.provider-badge) {
  opacity: 0.5;
}
.provider-row .main {
  align-items: center;
}
.logo {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  object-fit: cover;
  flex: none;
}
.compact .logo {
  width: 24px;
  height: 24px;
}
.name {
  font-weight: 600;
}
.profile {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  min-width: 0;
}
.profile select {
  min-width: 180px;
  max-width: 100%;
}
.hint {
  color: var(--text-muted);
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
@media (max-width: 480px) {
  .profile {
    width: 100%;
    padding-left: 46px;
  }
  .profile select {
    flex: 1 1 auto;
    min-width: 0;
  }
}
</style>
