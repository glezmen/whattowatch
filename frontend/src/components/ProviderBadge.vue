<script setup lang="ts">
import { computed } from 'vue'
import { PROVIDER_BY_KEY } from '../providers'

const props = defineProps<{ providerKey: string; fallbackName?: string; uncertain?: boolean }>()
const info = computed(() => PROVIDER_BY_KEY[props.providerKey])
</script>

<template>
  <span class="provider-badge" :class="{ uncertain }" :style="{ background: info?.color ?? 'var(--text-muted)' }">
    {{ info?.name ?? fallbackName ?? providerKey }}<sup v-if="uncertain" class="unc" aria-hidden="true">~</sup>
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
.provider-badge.uncertain {
  outline: 1px dashed currentColor;
  outline-offset: 1px;
}
.unc {
  margin-left: 2px;
  font-size: 0.7rem;
  line-height: 0;
}
</style>
