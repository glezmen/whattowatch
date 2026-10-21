<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { logout, me } from '../auth'
import ProfileAvatar from './ProfileAvatar.vue'

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const route = useRoute()

function onDocClick(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})

function switchProfile() {
  open.value = false
  void logout()
}
</script>

<template>
  <div v-if="me" ref="root" class="pm">
    <button
      type="button"
      class="pm-btn"
      :aria-expanded="open"
      aria-haspopup="menu"
      :aria-label="$t('profile.menuLabel', { name: me.name })"
      @click="open = !open"
    >
      <ProfileAvatar :name="me.name" :color="me.avatar_color" :size="28" />
      <span class="pm-name">{{ me.name }}</span>
    </button>
    <div v-if="open" class="pm-menu card" role="menu">
      <p class="small muted pm-head">
        {{ $t('profile.signedInAs', { name: me.name }) }}
        <span v-if="me.is_admin" class="tag">{{ $t('profile.admin') }}</span>
      </p>
      <button type="button" role="menuitem" class="pm-item" @click="switchProfile">{{ $t('profile.switch') }}</button>
      <RouterLink
        role="menuitem"
        class="pm-item"
        :to="{ path: '/beallitasok', hash: '#profil' }"
        :aria-current="route.path === '/beallitasok' ? 'page' : undefined"
        @click="open = false"
      >
        {{ $t('profile.settingsLink') }}
      </RouterLink>
    </div>
  </div>
</template>

<style scoped>
.pm {
  position: relative;
}
.pm-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px 2px 2px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: none;
}
.pm-btn:hover:not(:disabled) {
  background: var(--surface-2);
}
.pm-name {
  max-width: 12ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.92rem;
}
.pm-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  z-index: 60;
  min-width: 220px;
  display: flex;
  flex-direction: column;
  padding: 6px;
}
.pm-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 2px 6px 6px;
}
.pm-item {
  display: block;
  padding: 8px 10px;
  border: none;
  border-radius: var(--radius);
  background: none;
  color: var(--text);
  text-align: left;
  text-decoration: none;
  font-size: 0.92rem;
}
.pm-item:hover {
  background: var(--surface-2);
}
@media (max-width: 480px) {
  .pm-name {
    display: none;
  }
}
</style>
