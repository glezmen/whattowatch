<script setup lang="ts">
import { ref } from 'vue'
import { fmtStars } from '../format'

const props = defineProps<{ modelValue: number | null | undefined; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: number | null] }>()
const hover = ref(0)

const PATH = 'M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.5L12 17.3l-5.9 3.2 1.3-6.5L2.5 9.4l6.6-.8z'

/** Filled share (0, 50 or 100 %) of star n for the shown value. */
function fill(n: number): number {
  const v = hover.value || props.modelValue || 0
  if (v >= n) return 100
  if (v >= n - 0.5) return 50
  return 0
}

function onKey(e: KeyboardEvent) {
  if (props.disabled) return
  const cur = props.modelValue ?? 0
  let next: number | null = null
  if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = Math.min(5, cur + 0.5)
  else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = Math.max(0.5, cur - 0.5)
  if (next === null) return
  e.preventDefault()
  emit('update:modelValue', next)
}
</script>

<template>
  <div class="stars" @mouseleave="hover = 0">
    <div
      class="row"
      role="slider"
      tabindex="0"
      :aria-label="$t('stars.group')"
      aria-valuemin="0.5"
      aria-valuemax="5"
      :aria-valuenow="modelValue ?? undefined"
      :aria-disabled="disabled"
      @keydown="onKey"
    >
      <span v-for="n in 5" :key="n" class="star">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path class="bg" :d="PATH" />
        </svg>
        <svg
          class="fg"
          viewBox="0 0 24 24"
          width="20"
          height="20"
          aria-hidden="true"
          :style="{ clipPath: `inset(0 ${100 - fill(n)}% 0 0)` }"
        >
          <path :d="PATH" />
        </svg>
        <button
          type="button"
          class="half left"
          tabindex="-1"
          :aria-label="$t('stars.star', { n: fmtStars(n - 0.5) })"
          :disabled="disabled"
          @mouseenter="hover = n - 0.5"
          @click="emit('update:modelValue', n - 0.5)"
        />
        <button
          type="button"
          class="half right"
          tabindex="-1"
          :aria-label="$t('stars.star', { n })"
          :disabled="disabled"
          @mouseenter="hover = n"
          @click="emit('update:modelValue', n)"
        />
      </span>
    </div>
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
.row {
  display: inline-flex;
  gap: 2px;
}
.star {
  position: relative;
  display: inline-block;
  width: 24px;
  height: 24px;
  padding: 2px;
  box-sizing: border-box;
  line-height: 0;
}
.star svg {
  display: block;
}
.star .fg {
  position: absolute;
  top: 2px;
  left: 2px;
}
.bg {
  fill: none;
  stroke: var(--text-muted);
  stroke-width: 1.5;
}
.fg path {
  fill: var(--star);
  stroke: var(--star);
  stroke-width: 1.5;
}
.half {
  position: absolute;
  top: 0;
  height: 100%;
  width: 50%;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
}
.half.left {
  left: 0;
}
.half.right {
  right: 0;
}
.clear {
  margin-left: 4px;
}
</style>
