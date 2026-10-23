<script setup lang="ts">
import { activeGenre, excludedGenres, unexclude } from '../genreFilter'

defineProps<{ hideIncluded?: boolean }>()
</script>

<template>
  <span v-if="activeGenre && !hideIncluded" class="genre-pill small">
    {{ $t('genreFilter.label') }}: <b>{{ activeGenre }}</b>
    <button type="button" class="pill-clear" :aria-label="$t('genreFilter.clear')" :title="$t('genreFilter.clear')" @click="activeGenre = ''">
      &times;
    </button>
  </span>
  <span v-if="excludedGenres.length" class="genre-pill excluded small">
    {{ $t('genreFilter.excludedLabel') }}:
    <template v-for="g in excludedGenres" :key="g">
      <b>{{ g }}</b>
      <button type="button" class="pill-clear" :aria-label="$t('genreFilter.removeExcluded', { genre: g })" :title="$t('genreFilter.removeExcluded', { genre: g })" @click="unexclude(g)">
        &times;
      </button>
    </template>
  </span>
</template>

<style scoped>
.genre-pill {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding: 1px 4px 1px 10px;
  border: 1px solid var(--accent);
  border-radius: 999px;
  background: var(--surface-2);
}
.genre-pill.excluded {
  border-color: var(--danger);
}
.pill-clear {
  border: none;
  background: transparent;
  padding: 0 6px;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
  color: var(--text-muted);
}
.pill-clear:hover {
  color: var(--text);
}
</style>
