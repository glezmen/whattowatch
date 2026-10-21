<script setup lang="ts">
defineProps<{ headingKey: string }>()
</script>

<template>
  <h1>{{ $t(headingKey) }}</h1>
  <p class="empty"><RouterLink to="/">{{ $t('common.backHome') }}</RouterLink></p>
</template>
