<script setup lang="ts">
import { activeGenre } from '../genreFilter'
</script>

<template>
  <span v-if="activeGenre" class="genre-pill small">
    {{ $t('genreFilter.label') }}: <b>{{ activeGenre }}</b>
    <button type="button" class="pill-clear" :aria-label="$t('genreFilter.clear')" :title="$t('genreFilter.clear')" @click="activeGenre = ''">
      &times;
    </button>
  </span>
</template>

<style scoped>
.genre-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 4px 1px 10px;
  border: 1px solid var(--accent);
  border-radius: 999px;
  background: var(--surface-2);
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
