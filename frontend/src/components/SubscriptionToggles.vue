<script setup lang="ts">
import { onMounted } from 'vue'
import { enabledCount, loadSubscriptions, subscriptions } from '../appSettings'
import ProviderRow from './ProviderRow.vue'

defineProps<{ compact?: boolean }>()

onMounted(() => void loadSubscriptions())
</script>

<template>
  <div class="subs">
    <p v-if="!subscriptions.loaded" class="small muted"><span class="spinner"></span> {{ $t('common.loading') }}</p>
    <template v-else>
      <ul>
        <ProviderRow v-for="s in subscriptions.items" :key="s.key" :item="s" :compact="compact" />
      </ul>
      <p v-if="enabledCount === 0" class="banner small warn-box" role="alert">{{ $t('subscriptions.none') }}</p>
    </template>
  </div>
</template>

<style scoped>
ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.warn-box {
  margin: 10px 0 0;
}
</style>
