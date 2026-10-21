<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ overview?: string | null }>()
const { t: tr, locale } = useI18n()
const text = computed(() => props.overview?.trim() || '')
const open = ref(false)
</script>

<template>
  <div class="desc">
    <button type="button" class="link small desc-toggle" :aria-expanded="open" @click="open = !open">
      {{ open ? '▾' : '▸' }} {{ tr('card.descToggle') }}
    </button>
    <p v-if="open" class="small desc-text">
      <template v-if="text">
        {{ text }}
        <span v-if="locale === 'en'" class="muted note">{{ tr('card.huDescNote') }}</span>
      </template>
      <span v-else class="muted">{{ tr('card.noDescription') }}</span>
    </p>
  </div>
</template>

<style scoped>
.desc {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
}
.desc-text {
  margin: 0;
  white-space: pre-line;
}
.note {
  display: block;
  margin-top: 4px;
  font-size: 0.7rem;
}
</style>
