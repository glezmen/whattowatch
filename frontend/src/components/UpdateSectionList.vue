<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, type UpdateItem, type UpdatesResponse, type UpdateSection } from '../api/client'
import UpdateItemCard from './UpdateItemCard.vue'

const props = defineProps<{
  section: UpdateSection
  heading?: string
  days: number
  emptyText?: string
  /** Sort by event date ascending, undated items last. */
  sortByDate?: boolean
}>()
const emit = defineEmits<{ status: [status: UpdatesResponse['source_status']] }>()

const items = ref<UpdateItem[]>([])
const loading = ref(false)
const failed = ref(false)

function dateOf(it: UpdateItem): string | null {
  const e = it.event
  if (e.payload?.undated === true) return null
  return e.leaving_at ?? e.arriving_at ?? e.event_date ?? null
}

const shown = computed(() => {
  if (!props.sortByDate) return items.value
  return [...items.value].sort((a, b) => {
    const da = dateOf(a)
    const db = dateOf(b)
    if (da === db) return 0
    if (da === null) return 1
    if (db === null) return -1
    return da < db ? -1 : 1
  })
})

async function load() {
  loading.value = true
  failed.value = false
  try {
    const res = await api.getUpdates(props.section, props.days)
    items.value = res.items
    emit('status', res.source_status)
  } catch {
    failed.value = true
    items.value = []
  } finally {
    loading.value = false
  }
}

watch(() => props.days, load)
onMounted(load)
</script>

<template>
  <section class="update-section">
    <h2 v-if="heading">
      {{ heading }}
      <span v-if="!loading && items.length" class="count small muted">({{ items.length }})</span>
    </h2>
    <p v-if="loading" class="empty"><span class="spinner"></span> {{ $t('common.loading') }}</p>
    <p v-else-if="failed" class="neutral small muted">{{ $t('updates.loadFailed') }}</p>
    <p v-else-if="!items.length" class="neutral small muted">{{ emptyText ?? $t('updates.emptyDefault') }}</p>
    <div v-else class="grid">
      <UpdateItemCard v-for="it in shown" :key="it.event.id" :item="it" :section="section" />
    </div>
  </section>
</template>

<style scoped>
.update-section {
  margin-bottom: 28px;
}
h2 {
  font-size: 1.15rem;
  margin: 0 0 10px;
}
.count {
  font-weight: 400;
}
.neutral {
  margin: 0;
  padding: 10px 0;
}
.empty {
  padding: 16px;
}
</style>
