<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ modelValue: number | null | undefined; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: number | null] }>()
const hover = ref(0)
</script>

<template>
  <div class="stars" role="radiogroup" :aria-label="$t('stars.group')" @mouseleave="hover = 0">
    <button
      v-for="n in 5"
      :key="n"
      type="button"
      class="star"
      role="radio"
      :aria-checked="modelValue === n"
      :aria-label="$t('stars.star', { n })"
      :class="{ on: (hover || modelValue || 0) >= n }"
      :disabled="disabled"
      @mouseenter="hover = n"
      @click="emit('update:modelValue', n)"
    >
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
        <path d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.5L12 17.3l-5.9 3.2 1.3-6.5L2.5 9.4l6.6-.8z" />
      </svg>
    </button>
    <button
      v-if="modelValue"
      type="button"
      class="icon-btn clear"
      :aria-label="$t('stars.clear')"
      :title="$t('stars.clear')"
      :disabled="disabled"
      @click="emit('update:modelValue', null)"
    >
      ×
    </button>
  </div>
</template>

<style scoped>
.stars {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.star {
  padding: 2px;
  border: none;
  background: none;
  cursor: pointer;
  line-height: 0;
}
.star path {
  fill: none;
  stroke: var(--text-muted);
  stroke-width: 1.5;
}
.star.on path {
  fill: var(--star);
  stroke: var(--star);
}
.clear {
  margin-left: 4px;
}
</style>
