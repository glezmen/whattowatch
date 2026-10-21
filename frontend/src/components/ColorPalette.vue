<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { AVATAR_COLORS } from '../auth'

const model = defineModel<string>({ required: true })
defineProps<{ label: string }>()
const { t } = useI18n()
</script>

<template>
  <div class="palette" role="radiogroup" :aria-label="label">
    <button
      v-for="(c, i) in AVATAR_COLORS"
      :key="c"
      type="button"
      role="radio"
      class="swatch"
      :class="{ active: model === c }"
      :aria-checked="model === c"
      :aria-label="t('profile.colorN', { n: i + 1 })"
      :style="{ background: c }"
      @click="model = c"
    ></button>
  </div>
</template>

<style scoped>
.palette {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.swatch {
  width: 28px;
  height: 28px;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 50%;
}
.swatch:hover:not(:disabled) {
  filter: brightness(1.1);
}
.swatch.active {
  border-color: var(--text);
  box-shadow: inset 0 0 0 2px var(--surface);
}
.swatch:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>
