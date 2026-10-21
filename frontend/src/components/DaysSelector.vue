<script setup lang="ts">
const days = defineModel<number>({ required: true })
const options = [7, 14, 31]
</script>

<template>
  <div class="segmented" role="group" :aria-label="$t('days.label')">
    <button v-for="d in options" :key="d" type="button" :class="{ active: days === d }" @click="days = d">{{ $t('days.n', { n: d }) }}</button>
  </div>
</template>
