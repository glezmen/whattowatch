<script setup lang="ts">
// Global "show titles on my watchlist" switch plus the "N hidden — show them" hint.
// `hidden` comes from the X-Filtered-Watchlist response header of the view's request(s).
import { includeWatchlist } from '../appSettings'
import ToggleSwitch from './ToggleSwitch.vue'

defineProps<{ hidden?: number }>()
</script>

<template>
  <ToggleSwitch v-model="includeWatchlist" class="small" :label="$t('watchlistToggle.label')">
    <template v-if="!includeWatchlist && (hidden ?? 0) > 0" #hint>
      <span role="status">
        {{ $t('watchlistToggle.hidden', { n: hidden }, hidden ?? 0) }}
        <a href="#" @click.prevent="includeWatchlist = true">{{ $t('watchlistToggle.show') }}</a>
      </span>
    </template>
  </ToggleSwitch>
</template>
