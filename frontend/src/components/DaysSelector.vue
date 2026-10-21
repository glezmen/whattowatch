<script setup lang="ts">
const days = defineModel<number>({ required: true })
const options = [7, 14, 31]
</script>

<template>
  <div class="segmented" role="group" aria-label="Időszak">
    <button v-for="d in options" :key="d" type="button" :class="{ active: days === d }" @click="days = d">{{ d }} nap</button>
  </div>
</template>
