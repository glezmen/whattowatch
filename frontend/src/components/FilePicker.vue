<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ accept?: string; disabled?: boolean; modelValue: File | null; label?: string }>()
const emit = defineEmits<{ 'update:modelValue': [file: File | null] }>()
const { t } = useI18n()
const input = ref<HTMLInputElement | null>(null)

function onChange(e: Event) {
  emit('update:modelValue', (e.target as HTMLInputElement).files?.[0] ?? null)
}
function clear() {
  if (input.value) input.value.value = ''
  emit('update:modelValue', null)
}
// Keep the native input in sync when the parent resets the model.
watch(
  () => props.modelValue,
  (f) => {
    if (!f && input.value) input.value.value = ''
  },
)
</script>

<template>
  <div class="file-picker">
    <label class="pick" :class="{ disabled }">
      <input ref="input" type="file" :accept="accept" :disabled="disabled" :aria-label="label ?? t('filePicker.choose')" @change="onChange" />
      <span>{{ label ?? t('filePicker.choose') }}</span>
    </label>
    <span class="name small" :class="{ muted: !modelValue }" :title="modelValue?.name">{{ modelValue ? modelValue.name : t('filePicker.none') }}</span>
    <button v-if="modelValue" type="button" class="icon-btn" :disabled="disabled" :aria-label="t('filePicker.clear')" :title="t('filePicker.clear')" @click="clear">×</button>
  </div>
</template>

<style scoped>
.file-picker {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  max-width: 100%;
  min-width: 0;
}
.pick {
  position: relative;
  display: inline-block;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  cursor: pointer;
}
.pick:hover:not(.disabled) {
  background: var(--surface-2);
}
.pick.disabled {
  opacity: 0.55;
  cursor: default;
}
.pick:focus-within {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.pick input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: inherit;
}
.name {
  min-width: 0;
  max-width: 16rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.name.muted {
  color: var(--text-muted);
}
</style>
