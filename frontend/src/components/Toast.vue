<script setup lang="ts">
import { dismissToast, toasts } from '../toast'
</script>

<template>
  <div class="toasts" aria-live="polite">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind" role="alert">
      <span>{{ t.text }}</span>
      <button class="icon-btn" type="button" aria-label="Bezárás" @click="dismissToast(t.id)">×</button>
    </div>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: 16px;
  bottom: 16px;
  left: 16px;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  z-index: 100;
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  display: flex;
  gap: 12px;
  align-items: flex-start;
  max-width: 420px;
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  box-shadow: var(--shadow);
}
.toast.error {
  border-left-color: var(--danger);
}
.toast span {
  flex: 1;
  white-space: pre-wrap;
}
</style>
