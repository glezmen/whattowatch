<script setup lang="ts">
import { ref } from 'vue'
import NotificationBell from './NotificationBell.vue'

const open = ref(false)
const links = [
  { to: '/', label: 'Neked ajánljuk' },
  { to: '/felfedezes', label: 'Felfedezés' },
  { to: '/beszelgetes', label: 'Beszélgetős ajánló' },
  { to: '/watchlist', label: 'Watchlist' },
  { to: '/elozmenyek', label: 'Előzmények' },
  { to: '/ujdonsagok', label: 'Újdonságok' },
  { to: '/tavozik', label: 'Hamarosan távozik' },
  { to: '/feloldas', label: 'Címfeloldás' },
  { to: '/sync', label: 'Import és szinkron' },
  { to: '/beallitasok', label: 'Beállítások' },
]
</script>

<template>
  <header class="nav">
    <RouterLink to="/" class="brand">Mit nézzek?</RouterLink>
    <div class="right">
      <NotificationBell />
      <button class="icon-btn menu" type="button" :aria-expanded="open" aria-label="Menü" @click="open = !open">
        <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 6h18M3 12h18M3 18h18" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
      </button>
    </div>
    <nav :class="{ open }">
      <RouterLink v-for="l in links" :key="l.to" :to="l.to" @click="open = false">{{ l.label }}</RouterLink>
    </nav>
  </header>
</template>

<style scoped>
.nav {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 16px;
  padding: 8px 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.brand {
  font-weight: 700;
  font-size: 1.1rem;
  color: var(--text);
  text-decoration: none;
}
nav {
  order: 2;
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
}
nav a {
  padding: 6px 10px;
  border-radius: var(--radius);
  color: var(--text-muted);
  text-decoration: none;
  font-size: 0.92rem;
}
nav a:hover {
  background: var(--surface-2);
}
nav a.router-link-exact-active {
  color: var(--text);
  background: var(--surface-2);
  font-weight: 600;
}
.right {
  order: 3;
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}
.menu {
  display: none;
}
@media (max-width: 760px) {
  .menu {
    display: inline-flex;
  }
  nav {
    order: 4;
    display: none;
    width: 100%;
    flex-direction: column;
  }
  nav.open {
    display: flex;
  }
}
</style>
