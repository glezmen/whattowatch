<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TitleCard } from '../api/client'
import { displayTitle } from '../titles'

const IMG_BASE = 'https://image.tmdb.org/t/p/w342'

const props = defineProps<{ title: TitleCard }>()
const { t: tr, locale } = useI18n()
const mainTitle = computed(() => displayTitle(props.title))
const overview = computed(() => props.title.overview_hu?.trim() || '')
const tapOpen = ref(false)
function onPosterTap() {
  if (typeof window !== 'undefined' && window.matchMedia?.('(hover: none)').matches) tapOpen.value = !tapOpen.value
}
</script>

<template>
  <div class="poster-wrap" :class="{ 'tap-open': tapOpen }" @click="onPosterTap">
    <img v-if="title.poster_path" class="poster" :src="IMG_BASE + title.poster_path" :alt="tr('card.poster', { title: mainTitle })" loading="lazy" />
    <div v-else class="poster placeholder" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="32" height="32">
        <path d="M4 5h16v14H4zM4 9h16M8 5v4M12 5v4M16 5v4" fill="none" stroke="currentColor" stroke-width="1.5" />
      </svg>
    </div>
    <div class="poster-overlay small" aria-hidden="true">
      <template v-if="overview">
        {{ overview }}
        <span v-if="locale === 'en'" class="muted note">{{ tr('card.huDescNote') }}</span>
      </template>
      <template v-else>{{ tr('card.noDescription') }}</template>
    </div>
  </div>
</template>

<style scoped>
.poster {
  width: 100%;
  aspect-ratio: 2 / 3;
  object-fit: cover;
  border-radius: 6px;
  background: var(--surface-2);
}
.poster-wrap {
  position: relative;
  flex-shrink: 0;
  width: var(--poster-width, 92px);
  align-self: flex-start;
}
.poster-overlay {
  position: absolute;
  inset: 0;
  overflow-y: auto;
  padding: 6px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--surface-2) 94%, transparent);
  color: var(--text);
  font-size: 0.7rem;
  line-height: 1.35;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
}
.poster-wrap.tap-open .poster-overlay {
  opacity: 1;
  pointer-events: auto;
}
@media (hover: hover) {
  .poster-wrap:hover .poster-overlay {
    opacity: 1;
    pointer-events: auto;
  }
}
.note {
  display: block;
  margin-top: 4px;
  font-size: 0.7rem;
}
.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
</style>
