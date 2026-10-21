<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, type NotificationItem } from '../api/client'
import { eventRoute, eventTypeLabel, fmtDate } from '../format'
import { displayTitle } from '../titles'

const POLL_MS = 60_000

const route = useRoute()
const unread = ref(0)
const open = ref(false)
const items = ref<NotificationItem[]>([])
const loading = ref(false)
const root = ref<HTMLElement | null>(null)
let timer: ReturnType<typeof setInterval> | undefined

async function refreshCount() {
  try {
    unread.value = (await api.getNotificationCount()).unread
  } catch {
    // quiet: backend may be down; keep the last known count
  }
}

async function loadList() {
  loading.value = true
  try {
    items.value = await api.getNotifications(true)
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function toggle() {
  open.value = !open.value
  if (open.value) loadList()
}

async function markRead(n: NotificationItem) {
  try {
    await api.markNotificationRead(n.id)
    items.value = items.value.filter((x) => x.id !== n.id)
    unread.value = Math.max(0, unread.value - 1)
  } catch {
    // toast already shown
  }
}

function titleOf(n: NotificationItem): string {
  return displayTitle(n.title)
}

function onDocClick(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}

watch(
  () => route.fullPath,
  () => {
    open.value = false
    refreshCount()
  },
)

onMounted(() => {
  refreshCount()
  timer = setInterval(refreshCount, POLL_MS)
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="root" class="bell-wrap">
    <button
      type="button"
      class="icon-btn bell"
      :aria-expanded="open"
      :aria-label="unread ? $t('bell.labelUnread', { n: unread }) : $t('bell.label')"
      @click="toggle"
    >
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20a2 2 0 0 0 4 0"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linejoin="round"
        />
      </svg>
      <span v-if="unread" class="badge">{{ unread > 99 ? '99+' : unread }}</span>
    </button>
    <div v-if="open" class="panel card" role="dialog" :aria-label="$t('bell.dialog')">
      <h2>{{ $t('bell.heading') }}</h2>
      <p v-if="loading" class="small muted"><span class="spinner"></span> {{ $t('common.loading') }}</p>
      <p v-else-if="!items.length" class="small muted">{{ $t('bell.empty') }}</p>
      <ul v-else>
        <li v-for="n in items" :key="n.id">
          <RouterLink :to="eventRoute(n.event_type)" class="item-link" @click="open = false">
            <span class="tag">{{ eventTypeLabel(n.event_type) }}</span>
            <strong>{{ titleOf(n) }}</strong>
            <span class="small muted">{{ fmtDate(n.event_date) }}</span>
          </RouterLink>
          <button type="button" class="link small" @click="markRead(n)">{{ $t('bell.markRead') }}</button>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.bell-wrap {
  position: relative;
}
.bell {
  position: relative;
  display: inline-flex;
  padding: 4px;
}
.badge {
  position: absolute;
  top: -2px;
  right: -4px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 999px;
  background: var(--danger);
  color: #fff;
  font-size: 0.7rem;
  font-weight: 700;
  line-height: 18px;
  text-align: center;
}
.panel {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  width: min(360px, calc(100vw - 32px));
  max-height: 70vh;
  overflow-y: auto;
  z-index: 60;
}
h2 {
  font-size: 1rem;
  margin: 0 0 8px;
}
ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
li:last-child {
  border-bottom: none;
}
.item-link {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 8px;
  color: var(--text);
  text-decoration: none;
}
.item-link:hover strong {
  text-decoration: underline;
}
</style>
