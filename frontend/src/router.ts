import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomeView, meta: { title: 'Neked ajánljuk' } },
    { path: '/felfedezes', component: () => import('./views/DiscoverView.vue'), meta: { title: 'Felfedezés' } },
    { path: '/beszelgetes', component: () => import('./views/ChatView.vue'), meta: { title: 'Beszélgetős ajánló' } },
    { path: '/watchlist', component: () => import('./views/WatchlistView.vue'), meta: { title: 'Watchlist' } },
    { path: '/elozmenyek', component: () => import('./views/HistoryView.vue'), meta: { title: 'Előzmények' } },
    { path: '/feloldas', component: () => import('./views/ResolutionView.vue'), meta: { title: 'Címfeloldás' } },
    { path: '/sync', component: () => import('./views/SyncView.vue'), meta: { title: 'Import és szinkron' } },
    { path: '/beallitasok', component: () => import('./views/SettingsView.vue'), meta: { title: 'Beállítások' } },
    {
      path: '/ujdonsagok',
      component: () => import('./views/PlaceholderView.vue'),
      props: { heading: 'Újdonságok', what: 'új megjelenések, streamingpremierek és új évadok' },
      meta: { title: 'Újdonságok' },
    },
    {
      path: '/tavozik',
      component: () => import('./views/PlaceholderView.vue'),
      props: { heading: 'Hamarosan távozik', what: 'igazolt kivezetési dátumok' },
      meta: { title: 'Hamarosan távozik' },
    },
    { path: '/:rest(.*)*', component: () => import('./views/PlaceholderView.vue'), props: { heading: 'Nincs ilyen oldal' } },
  ],
})

router.afterEach((to) => {
  const t = to.meta.title
  document.title = typeof t === 'string' ? `${t} · Mit nézzek?` : 'Mit nézzek?'
})
