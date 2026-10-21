<script setup lang="ts">
import { computed } from 'vue'
import { PROVIDER_BY_KEY } from '../providers'

const props = defineProps<{ providerKey: string; fallbackName?: string }>()
const info = computed(() => PROVIDER_BY_KEY[props.providerKey])
</script>

<template>
  <span class="provider-badge" :style="{ background: info?.color ?? 'var(--text-muted)' }">
    {{ info?.name ?? fallbackName ?? providerKey }}
  </span>
</template>

<style scoped>
.provider-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  color: #fff;
  font-size: 0.75rem;
  font-weight: 600;
  white-space: nowrap;
}
</style>
