<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { setLanguage } from '../appSettings'
import { UI_LANGS } from '../i18n'

defineProps<{ compact?: boolean }>()
const { locale, t } = useI18n()
</script>

<template>
  <div class="segmented lang-switch" :class="{ compact }" role="group" :aria-label="t('nav.language')">
    <button
      v-for="l in UI_LANGS"
      :key="l"
      type="button"
      :class="{ active: locale === l }"
      :aria-pressed="locale === l"
      :lang="l"
      :title="t(`lang.${l}`)"
      @click="setLanguage(l)"
    >
      {{ compact ? t(`lang.${l}Short`) : t(`lang.${l}`) }}
    </button>
  </div>
</template>

<style scoped>
.lang-switch.compact button {
  padding: 2px 8px;
  font-size: 0.8rem;
}
</style>
