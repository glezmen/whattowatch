<script setup lang="ts">
// Accessible on/off switch: a native <button role="switch"> (Space/Enter come for free),
// label on the right (prop or default slot), optional hint under it (prop or `hint` slot).
import { useId, useSlots } from 'vue'

const model = defineModel<boolean>({ default: false })
const props = defineProps<{ label?: string; hint?: string; disabled?: boolean }>()
const slots = useSlots()

const id = useId()
const labelId = `${id}-label`
const hintId = `${id}-hint`
// Plain function, not computed: slots are not reactive, so re-check on every render.
const hasHint = () => !!props.hint || !!slots.hint

function toggle() {
  if (!props.disabled) model.value = !model.value
}
</script>

<template>
  <span class="ts" :class="{ on: model, disabled }">
    <button
      :id="id"
      type="button"
      role="switch"
      class="ts-track"
      :aria-checked="model"
      :aria-labelledby="labelId"
      :aria-describedby="hasHint() ? hintId : undefined"
      :disabled="disabled"
      @click="toggle"
    >
      <span class="ts-knob" aria-hidden="true"></span>
    </button>
    <span class="ts-text">
      <label :id="labelId" :for="id" class="ts-label"><slot>{{ label }}</slot></label>
      <span v-if="hasHint()" :id="hintId" class="ts-hint small muted"><slot name="hint">{{ hint }}</slot></span>
    </span>
  </span>
</template>

<style scoped>
.ts {
  display: inline-flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}
.ts-track {
  position: relative;
  flex: none;
  width: 36px;
  height: 20px;
  margin-top: 1px;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface-2);
  transition:
    background-color 0.15s ease,
    border-color 0.15s ease;
}
.ts-track:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--text-muted);
}
.ts-track:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.ts-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--text-muted);
  box-shadow: 0 1px 2px rgb(0 0 0 / 0.25);
  transition: transform 0.15s ease;
}
.on .ts-track,
.on .ts-track:hover:not(:disabled) {
  background: var(--accent);
  border-color: var(--accent);
}
.on .ts-knob {
  transform: translateX(16px);
  background: var(--accent-text);
}
.ts-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.ts-label {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-height: 22px;
  cursor: pointer;
}
.disabled .ts-label {
  cursor: default;
  opacity: 0.6;
}
@media (prefers-reduced-motion: reduce) {
  .ts-track,
  .ts-knob {
    transition: none;
  }
}
</style>
