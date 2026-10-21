<script setup lang="ts">
// Global "show titles on my watchlist" checkbox plus the "N hidden — show them" hint.
// `hidden` comes from the X-Filtered-Watchlist response header of the view's request(s).
import { includeWatchlist } from '../appSettings'

defineProps<{ hidden?: number }>()
</script>

<template>
  <span class="wl-toggle small">
    <label><input v-model="includeWatchlist" type="checkbox" /> {{ $t('watchlistToggle.label') }}</label>
    <span v-if="!includeWatchlist && (hidden ?? 0) > 0" class="muted" role="status">
      {{ $t('watchlistToggle.hidden', { n: hidden }, hidden ?? 0) }}
      <a href="#" @click.prevent="includeWatchlist = true">{{ $t('watchlistToggle.show') }}</a>
    </span>
  </span>
</template>

<style scoped>
.wl-toggle {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
}
</style>
