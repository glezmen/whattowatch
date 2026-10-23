<script setup lang="ts">
import type { SortOrder } from '../sortOrder'

const sort = defineModel<SortOrder>({ required: true })
defineProps<{ defaultLabel: string }>()
</script>

<template>
  <select v-model="sort" class="sort-select" :aria-label="$t('sort.label')" :title="$t('sort.label')">
    <option value="leaving">{{ $t('sort.leaving') }}</option>
    <option value="rating">{{ $t('sort.rating') }}</option>
    <option value="default">{{ defaultLabel }}</option>
  </select>
</template>

<style scoped>
.sort-select {
  max-width: 100%;
}
</style>
