<script setup lang="ts">
import { onMounted } from 'vue'
import { enabledCount, loadSubscriptions, setSubscription, subscriptions } from '../appSettings'
import ProviderBadge from './ProviderBadge.vue'

onMounted(() => void loadSubscriptions())

function onChange(key: string, e: Event) {
  void setSubscription(key, (e.target as HTMLInputElement).checked)
}
</script>

<template>
  <div class="subs">
    <p v-if="!subscriptions.loaded" class="small muted"><span class="spinner"></span> {{ $t('common.loading') }}</p>
    <template v-else>
      <ul>
        <li v-for="s in subscriptions.items" :key="s.key">
          <label>
            <input type="checkbox" :checked="s.enabled" @change="onChange(s.key, $event)" />
            <ProviderBadge :provider-key="s.key" :fallback-name="s.display_name" />
          </label>
        </li>
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
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}
label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 4px 0;
}
input[type='checkbox'] {
  width: 18px;
  height: 18px;
}
.warn-box {
  margin: 10px 0 0;
}
</style>
