<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { appSettings, enabledCount, loadSubscriptions, subscriptions, ui, updateAppSettings } from '../appSettings'
import SubscriptionToggles from './SubscriptionToggles.vue'

const emit = defineEmits<{ done: [] }>()
const { t } = useI18n()

// Dismissing (×) hides the panel for this page session only; "Kész" persists onboarding_done.
const finishing = ref(false)

onMounted(() => void loadSubscriptions())

const visible = computed(() => {
  if (ui.onboardingDismissed || !appSettings.loaded || !appSettings.data) return false
  const noSubs = subscriptions.loaded && enabledCount.value === 0
  return !appSettings.data.onboarding_done || noSubs
})

async function finish() {
  finishing.value = true
  const ok = await updateAppSettings({ onboarding_done: true })
  finishing.value = false
  if (ok) {
    ui.onboardingDismissed = true
    emit('done')
  }
}
</script>

<template>
  <section v-if="visible" class="card onboarding" :aria-label="t('onboarding.title')">
    <header>
      <h2>{{ t('onboarding.title') }}</h2>
      <button type="button" class="icon-btn" :aria-label="t('onboarding.dismiss')" :title="t('onboarding.dismiss')" @click="ui.onboardingDismissed = true">×</button>
    </header>
    <p class="small muted">{{ t('onboarding.intro') }}</p>
    <ol>
      <li>
        <strong>{{ t('onboarding.step1') }}</strong>
        <p class="small muted">{{ t('onboarding.step1Hint') }}</p>
        <SubscriptionToggles compact />
      </li>
      <li>
        <strong>{{ t('onboarding.step2') }}</strong>
        <p class="small muted">{{ t('onboarding.step2Hint') }}</p>
        <RouterLink to="/import">{{ t('onboarding.step2Link') }} →</RouterLink>
      </li>
      <li>
        <strong>{{ t('onboarding.step3') }}</strong>
        <p class="small muted">{{ t('onboarding.step3Hint') }}</p>
        <button type="button" class="primary" :disabled="finishing" @click="finish">
          <span v-if="finishing" class="spinner" aria-hidden="true"></span>
          {{ t('onboarding.done') }}
        </button>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.onboarding {
  margin-bottom: 20px;
  border-left: 4px solid var(--accent);
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
h2 {
  margin: 0;
  font-size: 1.15rem;
}
ol {
  margin: 8px 0 0;
  padding-left: 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
li p {
  margin: 2px 0 6px;
}
</style>
