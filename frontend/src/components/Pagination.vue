<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ total: number; limit: number; offset: number }>()
const emit = defineEmits<{ change: [offset: number] }>()

const page = computed(() => Math.floor(props.offset / props.limit) + 1)
const pages = computed(() => Math.max(1, Math.ceil(props.total / props.limit)))
</script>

<template>
  <div v-if="pages > 1" class="pagination">
    <button type="button" :disabled="page <= 1" @click="emit('change', offset - limit)">‹ Előző</button>
    <span>{{ page }} / {{ pages }} oldal ({{ total }} találat)</span>
    <button type="button" :disabled="page >= pages" @click="emit('change', offset + limit)">Következő ›</button>
  </div>
  <p v-else class="muted small">{{ total }} találat</p>
</template>

<style scoped>
.pagination {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: center;
  margin: 16px 0;
}
</style>
